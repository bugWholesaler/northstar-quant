from pathlib import Path

from ashare.data.synthetic import generate

if __name__ == "__main__":
    generate(Path("fixtures/synthetic"))
