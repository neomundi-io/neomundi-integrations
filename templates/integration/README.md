<!-- Replace every <PLACEHOLDER>. Delete this comment when you are done. -->
<div align="center">

[![English](https://img.shields.io/badge/EN-English-2563eb?style=flat-square)](README.md) · [![Français](https://img.shields.io/badge/FR-Fran%C3%A7ais-6b7280?style=flat-square)](README_FR.md)

# <TECHNOLOGY> × NeoMundi

**<One sentence saying what a developer gets.>**

**[Quickstart](#quickstart)** · **[What it refuses to do](#refusals)** · **[Notebook](notebooks/quickstart.ipynb)** · **[Limitations](LIMITATIONS.md)** · **[Cost](#what-it-costs)**

</div>

---

## What this is

<What it does, in two or three sentences. Say plainly that it drives the
`neomundi` SDK and reimplements nothing.>

## What it is not

- **Not an evaluator.** It transports measurements; it does not interpret them.
- **Not a tracer.** There is no trace-only route: closing a measurement is a billed observation.
- **Not a truth checker.** NeoMundi verifies integrity and authenticity, not whether an answer was true.

## Install

<Honest install line. If the package is not on PyPI, say so and give the
repository install. CI refuses a copyable `pip install <name>` that fails.>

## Quickstart

```python
<Three lines. No more.>
```

## Refusals

<The table of what it refuses to do and the status each produces. This is
the section a developer should read before trusting any number.>

## What it costs

🔴 **Every submitted observation is a billed measurement.** <State the
sampling control and the kill switch here, not further down.>

## Honest status

| | |
|---|---|
| Offline test suite | <N> tests, green on <Python versions> |
| Real execution | <N observations, how many re-read as persisted> |
| Maturity | <experimental / beta / ...> |

Read [LIMITATIONS.md](LIMITATIONS.md) before drawing a conclusion from any score.

## License

Apache-2.0. See [LICENSE](../../LICENSE).
