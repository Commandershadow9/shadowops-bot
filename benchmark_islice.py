import timeit
import itertools

ram_patterns = {f"model_{i}": {"avg_ram_total_gb": i, "best_cleanup_method": "method"} for i in range(100000)}

def using_list():
    for k, v in list(ram_patterns.items())[:5]:
        pass

def using_islice():
    for k, v in itertools.islice(ram_patterns.items(), 5):
        pass

t1 = timeit.timeit(using_list, number=1000)
t2 = timeit.timeit(using_islice, number=1000)

print(f"using_list: {t1:.6f}s")
print(f"using_islice: {t2:.6f}s")
