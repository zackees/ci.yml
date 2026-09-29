from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    value: float


def build_benchmark_result(name: str, value: float) -> BenchmarkResult:
    return BenchmarkResult(name=name, value=value)


def main() -> None:
    print(build_benchmark_result("demo", 1.0))


if __name__ == "__main__":
    main()
