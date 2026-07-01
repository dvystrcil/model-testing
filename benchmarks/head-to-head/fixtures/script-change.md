`analyze.py` no longer writes its report to stdout by default. It now writes to
`benchmarks/results/<run-dir>/report.md` and prints only that path. A new
`--stdout` flag restores the old behavior of writing the report to stdout instead
of a file.

(No other script changed.)
