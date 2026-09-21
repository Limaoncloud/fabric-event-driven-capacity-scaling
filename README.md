# Fabric Event-Driven Capacity Scaling

## Business challenge

Fabric capacities can experience sudden demand that causes interactive delays and
throttling. Manually monitoring and resizing a capacity is slow and requires an operator
to react at the right time.

This demo automatically scales an Azure Fabric capacity when Fabric reports sustained
pressure. It uses event-driven monitoring instead of polling and applies guardrails such
as a cooldown and a maximum SKU. The resize uses an Azure Resource Manager **PATCH** so
only the SKU changes and capacity administrators are preserved.

## High-level architecture

```text
Fabric capacity Summary events
  -> Eventstream flattens capacity metrics
  -> Activator detects sustained pressure
  -> Power Automate reads the current SKU and applies guardrails
  -> Azure Resource Manager PATCH doubles the SKU, capped at F256
  -> Azure and later capacity events confirm the resize
```

The default demo rule monitors the average
`interactiveDelayThresholdPercentage` and triggers when it remains above 80 for two
minutes. The flow scales only an active capacity and enforces a 10-minute cooldown.

## Prerequisites

- A non-production Azure Fabric **F SKU** capacity and a Fabric workspace assigned to it.
- Permission to create Eventstream and Activator items in the workspace.
- Capacity administrator access to the monitored capacity.
- Power Automate Premium for the Azure Resource Manager or HTTP with Microsoft Entra ID
  actions.
- An identity for the flow with the Azure RBAC action
  `Microsoft.Fabric/capacities/write` at the capacity scope. Capacity administrator access
  alone does not grant this permission.
- Owner or User Access Administrator access if you need to assign the Azure role.

## How to use this repository

1. Follow the [demo runbook](docs/runbook.md) to create the Eventstream, flatten the
   capacity event fields, configure the Activator rule, test the solution, and roll it
   back.
2. Use the [Power Automate flow guide](docs/power-automate-flow.md) for the action settings,
   expressions, authentication, cooldown, cap, and SKU-only PATCH request.
3. Import or copy [the load-test notebook](notebooks/heavy_load_test.py) into a Fabric
   notebook when you need real capacity pressure for a demonstration.
4. Optionally use [the synthetic event script](scripts/send_synthetic_event.py) with an
   Eventstream custom endpoint for a deterministic trigger.
5. Run the test against a non-production capacity, verify the new SKU in Azure, restore
   the original SKU, and delete the Activator rule when finished to stop listener charges.

License: [MIT](LICENSE.md).