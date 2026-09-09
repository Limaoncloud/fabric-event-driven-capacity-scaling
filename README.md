# Fabric Event-Driven Capacity Scaling

Automatically scale a Microsoft Fabric capacity **up** when it comes under pressure,
driven by Fabric's own capacity health events — no polling, no external monitoring.

When interactive delay on the capacity climbs toward the throttling threshold, an
**Activator** rule calls a **Power Automate** flow that reads the current SKU, computes
the next size (double the current, capped), and resizes the capacity with an **Azure
Resource Manager PATCH**.

```
Fabric capacity Summary event (every 30s)
   -> Eventstream  (flatten the nested `data` object with Manage fields)
   -> Activator rule  (avg interactiveDelayThresholdPercentage > 80 for 2 min)
   -> Power Automate custom action  (read SKU -> double -> guard -> PATCH)
   -> Fabric capacity resized
   -> verified in Azure + later Summary events
```

## Why PATCH, not PUT

The resize uses an ARM **PATCH** (`Fabric Capacities - Update`) with a body containing
**only the SKU**. An earlier design used a full create-or-update (**PUT**), which
rewrites the whole resource and **silently removed the capacity administrators on every
run**. PATCH changes only the fields you send, so the administration list is never
touched.

## Repository contents

| Path | What it is |
|------|------------|
| `README.md` | This summary |
| `docs/runbook.md` | The full demo runbook: architecture, prerequisites, all six build steps, reducing trigger frequency, guardrails, troubleshooting, and cost |
| `docs/power-automate-flow.md` | The flow, action by action: Read -> Compose -> Condition -> HTTP PATCH, with the dynamic-doubling expressions, cooldown, cap, and authentication |
| `notebooks/heavy_load_test.py` | A PySpark notebook that pegs a capacity to trip the rule during a demo |
| `scripts/send_synthetic_event.py` | Sends a synthetic capacity event to a custom-endpoint eventstream, to trigger the rule deterministically |

## How it works

1. **Events** — Fabric emits a `Microsoft.Fabric.Capacity.Summary` event every 30
   seconds for each active, busy capacity. Each event carries `capacitySku`,
   `interactiveDelayThresholdPercentage` (a forward-looking ~10-minute utilization
   projection; interactive delay begins at 100%), and related fields — all nested under
   a parent `data` object.
2. **Flatten** — Activator can only monitor flat, top-level columns, so an eventstream
   **Manage fields** operator promotes `data.capacityId` and
   `data.interactiveDelayThresholdPercentage` to top-level columns.
3. **Detect** — an Activator object (keyed on `capacityId`) plus a rule: average
   `interactiveDelayThresholdPercentage` > 80 over 2 minutes.
4. **Act** — the rule calls a Power Automate custom action that resizes the capacity.
5. **Verify** — confirm the new SKU in the Azure portal and in later Summary events.

## Prerequisites

- A Fabric workspace on an **F SKU** capacity, and you are a **capacity admin** of it.
- **Power Automate** (Premium plan — the ARM connector / HTTP with Entra ID need it).
- The flow's identity has the Azure RBAC action **`Microsoft.Fabric/capacities/write`**
  on the capacity resource. **Being a capacity admin does NOT grant this** — assign
  Contributor or a custom role at the capacity scope.
- To grant that role you must be **Owner** or **User Access Administrator** on the
  capacity.

## Build steps (summary)

1. **Open + flatten** — Real-Time hub -> Fabric events -> Fabric capacity overview
   events -> **Create eventstream** -> add **Manage fields** to flatten -> Publish, with
   an Activator destination.
2. **Activator rule** — New object keyed on `capacityId`; rule on
   `interactiveDelayThresholdPercentage`, average > 80 over 2 min; action = a custom
   action named `Scale Fabric capacity`.
3. **Flow** — Activator trigger -> ARM **Read a resource** -> **Compose** (double + cap
   + cooldown) -> **Condition** -> **HTTP PATCH**. See `docs/power-automate-flow.md`.
4. **Test** — dry-run first (swap PATCH for a Compose), then real; verify each action's
   inputs/outputs; remember 202 is async and the cooldown skips repeats.
5. **Demonstrate** — start the rule, trip it (load or Test action), show activation
   history -> flow run -> new SKU in Azure.
6. **Roll back** — Azure portal -> capacity -> Scale -> Change size -> starting SKU;
   **delete** the rule to end the listener charge.

## Dynamic target SKU

Fabric F SKUs double at each step (F2, F4, F8, ... F256, F512), so "twice the current"
always lands on a valid SKU. The flow reads the current SKU, strips the `F`, doubles the
number, and re-adds the `F`, capped at **F256**:

```
TargetSku = trim(concat('F', string(min(mul(CurrentUnits, 2), 256))))
```

## Reducing trigger frequency

Summary events arrive every 30s, so a rule that fires on every breach calls the flow
dozens of times per overload. Reduce it on the Activator side first, then keep the flow
guards as a backstop:

- **Activate when the condition changes to true**, not on every occurrence (biggest win).
- **Widen the average window** to 10-15 min and raise the threshold to 85-90.
- **10-minute cooldown** in the flow (state persisted in a capacity tag).
- **Gate the PATCH on `state == Active`** so runs during an in-flight resize skip
  instead of erroring with "service is not ready to be updated".

## Guardrails and limitations

- **Doubling compounds** — the cooldown and F256 cap are mandatory, not optional.
- **Cap below F512** — scaling across the F256->F512 boundary can briefly interrupt the
  capacity and cancel running jobs.
- **Async resize** — PATCH returns `202 Accepted`; the SKU changes shortly after, so
  verify in Azure rather than trusting the flow's success alone.
- **Idle/paused capacity emits no events** — keep some activity running during a demo.
- **Best-effort delivery** — make the flow idempotent; use sustained averages, not a
  single event.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Rule builder shows only a `data` object | Not flattened — add a Manage fields operator to promote the fields, then bind the object |
| `New object` missing from the ribbon | Activator item isn't running — Start it, reopen |
| No live capacity events | Capacity idle or paused (both emit nothing); generate activity / resume |
| Ingestion paused: owner lacks permission | Eventstream owner lost capacity-admin (often a PUT flow wiping admins) — re-add as capacity admin, **Take over**, republish |
| `Create` greyed out on the rule | Set a simple email action first, save, then attach the custom action |
| No custom-action pop-up | Allow pop-ups + third-party cookies; confirm Power Automate access |
| Compose outputs the formula text | Enter it via the Expression editor (fx), not as plain text |
| `resource type could not be found ... api version` | Trailing space or wrong Short Resource Id — retype `2023-11-01`, use `capacities/<name>` |
| `The SKU 'F128 ' is unavailable` | Trailing space — wrap in `trim()` |
| Flow removes you as capacity admin | PUT rewrites the whole resource — switch to PATCH with a `sku`-only body |
| `403 ... Microsoft.Fabric/capacities/write` | Flow identity lacks the Azure role — assign it at the capacity scope (capacity admin is not enough) |
| `Service is not ready to be updated` | Capacity is mid-resize — gate the PATCH on `state == Active`; wait for Active and retry |

## Cost

The event + Activator machinery is ~0.64 CU-hours/day (~19/month) for one rule on one
capacity — a rounding error next to the capacity SKU, which is the real cost. You pay the
higher pay-as-you-go rate only while scaled up. Power Automate Premium is a fixed licence
cost. **Stopping a rule does not stop its cost — delete it** to end the event-listener
charge.

## References

- [Explore Fabric capacity overview events](https://learn.microsoft.com/en-us/fabric/real-time-hub/explore-fabric-capacity-overview-events)
- [Process events with the event processor editor (Manage fields)](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/process-events-using-event-processor-editor)
- [Trigger Power Automate flows from Activator](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/data-activator/activator-trigger-power-automate-flows)
- [Fabric Capacities - Update REST API (PATCH)](https://learn.microsoft.com/en-us/rest/api/microsoftfabric/fabric-capacities/update?view=rest-microsoftfabric-2023-11-01)
- [Scale your Fabric capacity](https://learn.microsoft.com/en-us/fabric/enterprise/scale-capacity)

## License

MIT — see [LICENSE.md](LICENSE.md).
