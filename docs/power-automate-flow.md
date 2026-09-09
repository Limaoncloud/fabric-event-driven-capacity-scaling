# Power Automate flow — action by action

Action order:

```
Activator trigger -> Read a resource -> Compose (x6) -> Condition -> (If yes) HTTP PATCH
```

## Activator custom action input

Define **one** input on the custom action:

| Input | Value |
|-------|-------|
| `capacityName` | `@capacityName` |

The flow reads everything else (current SKU, tags, state) from Azure, so no other inputs
are needed.

## Action 1 — Azure Resource Manager: Read a resource

| Field | Value |
|-------|-------|
| Subscription | `<subscription-id>` |
| Resource Group | `<resource-group>` |
| Resource Provider | `Microsoft.Fabric` |
| Short Resource Id | `capacities/<capacity-name>` |
| Client Api Version | `2023-11-01` |

> Type `2023-11-01` by hand — a trailing space causes "resource type could not be found".

## Compose actions (enter each via the Expression editor / fx)

| Compose name | Expression | Example |
|--------------|------------|---------|
| `CurrentSku` | `body('Read_a_resource')?['sku']?['name']` | `F64` |
| `CurrentUnits` | `int(substring(outputs('CurrentSku'), 1, sub(length(outputs('CurrentSku')), 1)))` | `64` |
| `TargetUnits` | `min(mul(outputs('CurrentUnits'), 2), 256)` | `128` |
| `TargetSku` | `trim(concat('F', string(outputs('TargetUnits'))))` | `F128` |
| `LastScaledAt` | `coalesce(body('Read_a_resource')?['tags']?['lastScaledAt'], '2000-01-01T00:00:00Z')` | old date if unset |
| `CooldownElapsed` | `greaterOrEquals(ticks(utcNow()), ticks(addMinutes(outputs('LastScaledAt'), 10)))` | `true` |

- `min(..., 256)` is the **cap**.
- `10` in `CooldownElapsed` is the **cooldown in minutes**.
- The cooldown state lives in the capacity's `lastScaledAt` **tag**, because each
  Activator event is a separate flow run with no memory.

## Condition (rows joined by And)

| Left | Operator | Right |
|------|----------|-------|
| `@outputs('CooldownElapsed')` | is equal to | `true` |
| `@outputs('CurrentUnits')` | is less than | `256` |
| `@body('Read_a_resource')?['properties']?['state']` | is equal to | `Active` |

Put the PATCH in the **If yes** branch. The third row prevents "service is not ready to
be updated" by skipping while a resize is still applying.

## Action 2 — HTTP PATCH (If yes branch)

| Setting | Value |
|---------|-------|
| Method | `PATCH` |
| URI | `https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Fabric/capacities/<name>?api-version=2023-11-01` |
| Header | `Content-Type: application/json` |

Body (SKU only — simplest, and cannot touch administration):

```json
{ "sku": { "name": "@{trim(outputs('TargetSku'))}", "tier": "Fabric" } }
```

Body (SKU + cooldown tag, merged to keep other tags):

```json
{ "sku": { "name": "@{trim(outputs('TargetSku'))}", "tier": "Fabric" },
  "tags": @{union(coalesce(body('Read_a_resource')?['tags'], json('{}')), json(concat('{"lastScaledAt":"', utcNow(), '"}')))} }
```

## Authentication

Do **not** add an `Authorization` header by hand — the HTTP action attaches the token.

**Easiest (demo):** use the **HTTP with Microsoft Entra ID** action. It authenticates via
a sign-in connection — no client ID, secret, tenant, or audience. Set both the Base
Resource URL and the Entra ID Resource URI to `https://management.azure.com` and sign in.

**Service principal:** on the plain HTTP action, open **Show advanced options** to reveal
the **Authentication** field:

| Field | Value |
|-------|-------|
| Authentication | `Active Directory OAuth` |
| Authority | `https://login.microsoftonline.com` |
| Tenant | `<tenant-id>` |
| Audience | `https://management.azure.com/` |
| Client ID | `<app-registration-client-id>` |
| Credential Type | `Secret` |
| Secret | `<client-secret-value>` |

Either identity needs `Microsoft.Fabric/capacities/write` on the capacity. A **403** means
that role is missing, not that the auth is wrong.

## Testing

- Save the flow first (Test is disabled otherwise).
- First run: **Test -> Manually**, then fire once from Activator (**Test action**).
- After that: **Test -> Automatically -> use data from a previous run** to replay.
- Dry run: temporarily replace the PATCH with a Compose that outputs `TargetSku`.
- `202 Accepted` is async — verify the SKU in Azure. The cooldown skips repeats.
