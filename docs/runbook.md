# Fabric Event-Driven Capacity Scaling — Demo Runbook

Scaling a Fabric capacity from capacity health events, using Activator, Power Automate,
and an Azure Resource Manager PATCH.

## Purpose and outcome

This demo shows how Microsoft Fabric can react to its own capacity health signals. Fabric
emits capacity Summary events every 30 seconds in Real-Time hub. An eventstream flattens
those events, an Activator rule evaluates them, and the rule calls a Power Automate custom
action. The flow reads the current SKU, computes the next size as twice the current one,
and resizes the capacity with an Azure Resource Manager PATCH. The presenter then verifies
the new SKU in Azure and in later capacity events.

This version uses a **PATCH** to change only the SKU. An earlier design used a full
create-or-update (**PUT**), which rewrites the whole resource and silently removed the
capacity administrators on every run. PATCH changes only the fields you send, so the
administration list is never touched.

### Recommended demo scope

- Use a non-production capacity and a small, reversible change. Automate scale-up first
  and keep scale-down separate and more conservative.
- Use an allow-list and a cap so the automation can only change the named demo capacity
  and cannot exceed an approved SKU.
- Stop or delete the Activator rule after the demonstration and return the capacity to its
  starting SKU.

## Solution architecture

```
Fabric capacity Summary event
  -> Eventstream (flatten the nested data object)
  -> Activator rule
  -> Power Automate custom action (compute double, guard, PATCH)
  -> Fabric capacity resize
  -> verification event
```

| Component | Role | Demo configuration |
|-----------|------|--------------------|
| Fabric capacity overview events | Emit Summary events every 30s and State events on health changes | One non-production F SKU capacity |
| Eventstream (Manage fields) | Flatten the nested `data` object so each metric is a top-level column | Promote `data.capacityId` and `data.interactiveDelayThresholdPercentage` |
| Activator | Evaluate the flattened stream and call the custom action | Average `interactiveDelayThresholdPercentage` > 80 for 2 min |
| Power Automate custom action | Read current SKU, double it, apply cooldown and cap, then PATCH | Target = twice current, capped at F256 |
| Azure Resource Manager (PATCH) | Change only the SKU on the capacity resource | PATCH body `sku` only; api-version 2023-11-01 |
| Fabric capacity | Move to the target SKU and keep emitting events | e.g. F64 → F128 |

## Prerequisites and permissions

The most common blocker is the last row: the flow's identity needs an Azure role, which is
**not** the same as being a capacity admin.

| Area | Requirement |
|------|-------------|
| Fabric workspace | On an active Fabric or trial capacity; you can create eventstreams and Activator items |
| Capacity admin | You administer the capacity that emits the events (events are admin-scoped) |
| Demo capacity | An Azure Fabric F SKU capacity; record the starting SKU |
| Power Automate | A Power Automate plan (Premium for the ARM connector / HTTP with Entra ID) |
| Azure RBAC | The flow identity has `Microsoft.Fabric/capacities/write` at the capacity scope |
| Role to grant it | You are Owner or User Access Administrator on the capacity |

## Build the demo

### Step 1 — Open the events and flatten them

In Fabric, open Real-Time hub → Fabric events → Fabric capacity overview events. Select the
demo capacity. Summary events are emitted every 30s while the capacity is active and busy;
State events only on a health-state change.

Every metric arrives nested under a parent `data` object, and Activator can only monitor
flat, top-level columns. So select **Create eventstream**, not Set alert. Open it in Edit
mode, add a **Manage fields** operator from the Transform events menu, and promote:

| Nested source field | Flatten to | Why |
|---------------------|------------|-----|
| `data.capacityId` | `capacityId` | Unique Identifier for the Activator object |
| `data.interactiveDelayThresholdPercentage` | `interactiveDelayThresholdPercentage` | The property the rule monitors |
| `data.capacitySku` | `capacitySku` | Display/audit; the flow reads the live SKU from ARM |
| `data.interactiveRejectionThresholdPercentage` | `interactiveRejectionThresholdPercentage` | Optional escalation signal |

Select Refresh to confirm, then Publish and add an Activator destination.

### Step 2 — Create the Activator rule

Open the Activator item fed by the flattened stream and keep it running. In the Explorer
pane (left), select the stream → **New object** (appears only while the item is started).
Choose `capacityId` as the Unique Identifier. An object is simply the thing you are
monitoring (the capacity); the events are readings about it.

Select the `interactiveDelayThresholdPercentage` property → **New rule**: Monitor that
property, Add summarization → Average over 2 minutes with a 30-second step, Condition
greater than 80, Action = a new custom action named **Scale Fabric capacity**. The value is
forward-looking (average projected utilization over ~10 min; interactive delay begins at
100%), so 80 gives headroom to scale before users feel slowdown.

> If the rule builder shows only a `data` object, the stream was not flattened — return to
> Manage fields. If **Create** is greyed out, set the action to a simple *Send me an email*
> first so the rule is valid, save it, then reopen and attach the custom action.

### Step 3 — Build the Power Automate flow

In the rule → Action → New custom action → name it `Scale Fabric capacity`, define one
input: `capacityName` = `@capacityName`. Copy the connection string, Open flow builder, and
paste it into the prepopulated Activator trigger (it shows *Invalid parameters* until you do).

Action order: **Activator trigger → Read a resource → Compose → Condition → (If yes) HTTP PATCH.**

**Read a resource** (Azure Resource Manager): Subscription, Resource Group, Resource
Provider `Microsoft.Fabric`, Short Resource Id `capacities/<name>`, Client Api Version
`2023-11-01` (type it by hand — a trailing space causes "resource type could not be found").

**Compose actions** (enter each via the Expression editor / fx):

| Compose | Expression | Example |
|---------|------------|---------|
| `CurrentSku` | `body('Read_a_resource')?['sku']?['name']` | `F64` |
| `CurrentUnits` | `int(substring(outputs('CurrentSku'), 1, sub(length(outputs('CurrentSku')), 1)))` | `64` |
| `TargetUnits` | `min(mul(outputs('CurrentUnits'), 2), 256)` | `128` |
| `TargetSku` | `trim(concat('F', string(outputs('TargetUnits'))))` | `F128` |
| `LastScaledAt` | `coalesce(body('Read_a_resource')?['tags']?['lastScaledAt'], '2000-01-01T00:00:00Z')` | old date if unset |
| `CooldownElapsed` | `greaterOrEquals(ticks(utcNow()), ticks(addMinutes(outputs('LastScaledAt'), 10)))` | `true` |

`min(..., 256)` is the cap; `10` is the cooldown in minutes. State survives between runs via
the capacity's `lastScaledAt` tag, because each Activator event is a separate flow run.

**Condition** (rows joined by And), PATCH in the If yes branch:

| Left | Operator | Right |
|------|----------|-------|
| `@outputs('CooldownElapsed')` | is equal to | `true` |
| `@outputs('CurrentUnits')` | is less than | `256` |
| `@body('Read_a_resource')?['properties']?['state']` | is equal to | `Active` |

**HTTP PATCH** (If yes): method `PATCH`, URI
`https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Fabric/capacities/<name>?api-version=2023-11-01`,
header `Content-Type: application/json`.

Body (SKU only):

```json
{ "sku": { "name": "@{trim(outputs('TargetSku'))}", "tier": "Fabric" } }
```

Body (SKU + cooldown tag, merged to keep other tags):

```json
{ "sku": { "name": "@{trim(outputs('TargetSku'))}", "tier": "Fabric" },
  "tags": @{union(coalesce(body('Read_a_resource')?['tags'], json('{}')), json(concat('{"lastScaledAt":"', utcNow(), '"}')))} }
```

**Authentication** — do not add an Authorization header by hand. Easiest: use the **HTTP
with Microsoft Entra ID** action (sign-in connection, no secret; set Base Resource URL and
Entra ID Resource URI to `https://management.azure.com`). Or plain HTTP → Active Directory
OAuth with a service principal (Authority `https://login.microsoftonline.com`, Tenant,
Audience `https://management.azure.com/`, Client ID, Credential Type Secret). Either identity
needs `Microsoft.Fabric/capacities/write`; a 403 means that role is missing.

### Step 4 — Test the flow

Save first (Test is disabled otherwise). First run: **Test → Manually**, then fire once from
Activator (**Test action**). After that: **Test → Automatically → previous run** to replay.
Dry-run first by swapping the PATCH for a Compose that outputs `TargetSku`. Remember: `202
Accepted` is async (verify in Azure), and the 10-minute cooldown skips repeats.

### Step 5 — Start and demonstrate

Start the rule (ribbon, or Save and start). Trip it by temporarily lowering the threshold,
using Test action, or generating real load on the smallest SKU. Show activation history →
Power Automate run → new SKU in the Azure capacity Overview.

### Step 6 — Roll back and stop

Stop the rule, then restore the starting SKU in the Azure portal (capacity → Scale → Change
size → Resize). To end the ongoing cost, **delete** the rule — the event listener keeps
charging rule uptime until it is removed.

## Reducing trigger frequency

Summary events arrive every 30s, so a rule that fires on every breach calls the flow dozens
of times per overload. Apply all four levers:

| Lever | Where | Effect |
|-------|-------|--------|
| Activate when the condition changes to true, not on every occurrence | Activator rule setting | Collapses an overload episode into one activation (biggest win) |
| Widen the average to 10–15 min, raise threshold to 85–90 | Rule summarization/condition | Crosses the line far less often |
| 10-minute cooldown | Flow | Extra calls take the skip branch |
| Gate on `state == Active` | Flow Condition | Runs during an in-flight resize skip instead of erroring |

## Guardrails and limitations

| Consideration | Design response |
|---------------|-----------------|
| Doubling compounds across events | Cooldown and F256 cap are mandatory |
| PUT rewrites the whole resource and drops admins | Use PATCH with a `sku`-only body |
| Events are nested under `data` | Flatten with Manage fields before Activator |
| Best-effort delivery can duplicate/drop events | Idempotent flow; sustained averages, not a single event |
| Idle/paused capacity emits nothing | Keep activity running during the demo |
| F256→F512 boundary can interrupt the capacity | The F256 cap stays below it |
| Resize is async (202) | Verify in Azure or a later Summary event |
| Capacity admin ≠ Azure RBAC | Grant `capacities/write` at the capacity scope |

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Rule builder shows only a `data` object | Not flattened — add Manage fields, then bind the object |
| `New object` missing from the ribbon | Item isn't running — Start it, reopen |
| No live capacity events | Capacity idle or paused; generate activity / resume |
| Ingestion paused: owner lacks permission | Re-add owner as capacity admin, Take over, republish |
| `Create` greyed out on the rule | Set a simple email action first, save, then attach the custom action |
| No custom-action pop-up | Allow pop-ups + third-party cookies; confirm Power Automate access |
| Compose outputs the formula text | Enter it via the Expression editor (fx) |
| `resource type could not be found ... api version` | Trailing space / wrong Short Resource Id — retype `2023-11-01`, use `capacities/<name>` |
| `The SKU 'F128 ' is unavailable` | Trailing space — wrap in `trim()` |
| Flow removes you as capacity admin | PUT rewrites the resource — switch to PATCH with `sku` only |
| `403 ... capacities/write` | Assign the Azure role to the flow identity at the capacity scope |
| `Service is not ready to be updated` | Capacity mid-resize — gate on `state == Active`; wait for Active and retry |
| Failed to load Eventstream (network issue) | Confirm the capacity is Active (not paused/scaling); Take over / recreate the eventstream |

## Cost

The event + Activator machinery is ~0.64 CU-hours/day (~19/month) for one rule on one
capacity — a rounding error next to the capacity SKU, which is the real cost. You pay the
higher pay-as-you-go rate only while scaled up. Power Automate Premium is a fixed licence
cost. Stopping a rule does not stop its cost; **delete it** to end the listener charge.

## References

- [Explore Fabric capacity overview events](https://learn.microsoft.com/en-us/fabric/real-time-hub/explore-fabric-capacity-overview-events)
- [Process events with the event processor editor (Manage fields)](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/process-events-using-event-processor-editor)
- [Trigger Power Automate flows from Activator](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows)
- [Fabric Capacities - Update REST API (PATCH)](https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/update?view=rest-microsoftfabric-2023-11-01)
- [Scale your Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity)
