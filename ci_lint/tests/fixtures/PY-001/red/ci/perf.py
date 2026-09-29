from typing import Any


def build_benchmark_result(name: str, value: float) -> dict:
    return {"name": name, "value": value}


def main() -> None:
    extra: dict[str, Any] = {}
    print(build_benchmark_result("demo", 1.0), extra)


if __name__ == "__main__":
    main()
