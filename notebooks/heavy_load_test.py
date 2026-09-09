# Heavy Spark load generator for Microsoft Fabric
# -----------------------------------------------
# Purpose: peg a NON-PRODUCTION Fabric capacity so the Activator scale-up rule trips.
# Run this in a Fabric notebook. It launches several concurrent Spark job groups and
# sustains load, because the rejection/delay metrics only climb under sustained pressure.
#
# WARNING: this burns Capacity Units and costs money. It is meant to trip the rule and
# then STOP. Keep DURATION_MIN short and cancel the notebook once the scale-up fires.
# Tip: run on the smallest SKU (F2/F4) so it saturates fast, and for the fastest trip
# watch interactiveDelayThresholdPercentage (10-min signal) rather than rejection (1-hr).

# --- Cell 1: parameters -------------------------------------------------------
ROWS          = 300_000_000   # base rows per pass (lower first if you hit OOM)
PARTITIONS    = 800           # ~2-4x total cores keeps every executor busy
DURATION_MIN  = 15            # how long to sustain the load
PARALLEL_JOBS = 4             # concurrent Spark job groups from THIS notebook
MATH_ITERS    = 40            # CPU intensity per pass

# --- Cell 2: the heavy workload (CPU + shuffle bound, memory-flat) -----------
import time
from concurrent.futures import ThreadPoolExecutor
from pyspark.sql import functions as F


def heavy_pass(job_id: int, pass_id: int):
    df = (spark.range(0, ROWS)
              .repartition(PARTITIONS)
              .withColumn("v", F.rand(seed=job_id * 1000 + pass_id)))

    # CPU-bound: chained transcendental math (burns cores, stays memory-flat)
    col = F.col("v")
    for i in range(MATH_ITERS):
        col = F.sin(col) + F.cos(col * F.lit(i + 1)) + F.sqrt(F.abs(col) + F.lit(1.0))
    df = df.withColumn("v", col)

    # Shuffle-bound: wide aggregation on a moderate-cardinality key
    df = df.withColumn("g", (F.col("id") % F.lit(50_000)))
    agg = df.groupBy("g").agg(F.sum("v").alias("s"), F.count("*").alias("c"))

    # Sort + action forces full materialization (no caching -- we want recompute)
    return agg.orderBy(F.col("s").desc()).limit(10).agg(F.sum("s")).collect()[0][0]


# --- Cell 3: sustained, concurrent driver ------------------------------------
stop_at, passes = time.time() + DURATION_MIN * 60, 0


def worker(job_id: int):
    global passes
    p = 0
    while time.time() < stop_at:
        spark.sparkContext.setJobGroup(f"loadtest-{job_id}", f"capacity load {job_id}")
        heavy_pass(job_id, p)
        p += 1
        passes += 1
        print(f"[job {job_id}] pass {p} @ {time.strftime('%H:%M:%S')}")


with ThreadPoolExecutor(max_workers=PARALLEL_JOBS) as ex:
    [f.result() for f in [ex.submit(worker, j) for j in range(PARALLEL_JOBS)]]

print(f"Done -- {passes} passes over {DURATION_MIN} min")

# To push harder: raise PARALLEL_JOBS/ROWS/MATH_ITERS, or run this notebook in 2-3
# separate sessions at once so they compete for the same capacity.
