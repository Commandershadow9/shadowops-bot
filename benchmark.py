import time
import timeit
from typing import Dict, Any

def with_keys(d: Dict[str, Any]):
    alerts = []
    for k in d.keys():
        alerts.append(k)
    return alerts

def without_keys(d: Dict[str, Any]):
    alerts = []
    for k in d:
        alerts.append(k)
    return alerts

if __name__ == "__main__":
    test_dict = {f"key_{i}": i for i in range(1000)}

    time_with = timeit.timeit(lambda: with_keys(test_dict), number=10000)
    time_without = timeit.timeit(lambda: without_keys(test_dict), number=10000)

    print(f"With .keys(): {time_with:.5f}s")
    print(f"Without .keys(): {time_without:.5f}s")
    print(f"Improvement: {(time_with - time_without) / time_with * 100:.2f}%")
