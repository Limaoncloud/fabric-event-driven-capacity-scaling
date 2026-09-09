# Send a synthetic capacity event to a custom-endpoint eventstream.
# -----------------------------------------------------------------
# Fabric's native capacity events cannot be injected, so for a deterministic, repeatable
# test we push a synthetic event into a SEPARATE eventstream we control (a "custom
# endpoint" source), point a test Activator rule at it, and drive the metric on demand.
#
# Setup:
#   1. New Eventstream -> Add source -> Custom endpoint -> copy the Event Hub
#      "connection string - primary key" (Details -> SAS Key Authentication).
#   2. Add an Activator destination and Publish.
#   3. In Activator: New object keyed on capacityName; rule on the field you want to test
#      (e.g. interactiveRejectionThresholdPercentage > 60) -> action = Scale Fabric capacity.
#
# Run in a Fabric notebook (or anywhere with the connection string).

# %pip install azure-eventhub -q
from azure.eventhub import EventHubProducerClient, EventData
import json
import time

CONN_STR = "Endpoint=sb://eventstream-xxxx.servicebus.windows.net/;SharedAccessKeyName=key_xxx;SharedAccessKey=xxx;EntityPath=es_xxx"

# Put your REAL capacity name here so the flow's ARM read/resize hits the real capacity.
# The flow reads the current SKU from ARM, so capacitySku below is only for display.
CAPACITY_NAME = "<your-real-capacity-name>"


def make_event(rejection_pct: float, delay_pct: float) -> dict:
    return {
        "capacityName": CAPACITY_NAME,
        "capacitySku": "F64",
        "interactiveRejectionThresholdPercentage": rejection_pct,
        "interactiveDelayThresholdPercentage": delay_pct,
        "windowEndTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def send(events):
    producer = EventHubProducerClient.from_connection_string(CONN_STR)
    with producer:
        batch = producer.create_batch()
        for e in events:
            batch.add(EventData(json.dumps(e)))
        producer.send_batch(batch)
    print(f"sent {len(events)} event(s)")


if __name__ == "__main__":
    # Single event that trips a "rejection > 60" rule:
    send([make_event(rejection_pct=65, delay_pct=85)])

    # To test cooldown / dedup, send several 30s apart and confirm only the first resizes:
    # for _ in range(5):
    #     send([make_event(rejection_pct=65, delay_pct=85)])
    #     time.sleep(30)
