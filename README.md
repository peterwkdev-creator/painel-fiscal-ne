# Painel Fiscal do Nordeste

How much every one of the **1,793 municipalities** of Brazil's Northeast spends
on personnel, against the limit the law sets for it — with the source and the
collection date next to every figure.

Brazil's Fiscal Responsibility Law caps municipal executive personnel spending
at **54%** of net current revenue, with a **51.3%** prudential threshold that
already forbids new hiring once crossed. The data is public and free, published
by the National Treasury through SICONFI — but it arrives **one municipality per
request**, in 200+ lines of chart-of-accounts per query, with no comparison and
no reading. In practice nobody looks.

> **Status: ingestion and panel both build; the full regional sweep is still
> running.** Everything below runs against the live API today.

## Run it

Requires **Python 3.10+** and nothing else — standard library only, no
dependencies to install.

```bash
python -m fiscal ingerir-entes && python -m fiscal ingerir
```

The first command fetches the 1,793 municipalities in a single request. The
second sweeps their Fiscal Management Reports — one request each, roughly **57
minutes** for the whole region. It is **resumable**: interrupt it and run it
again, and it continues instead of re-reading.

```bash
python -m fiscal ingerir --limite 25      # stop after 25, to try it out
python -m fiscal resumo                   # count and average per state
python -m fiscal listar --acima-do-limite # only those over the threshold
python -m fiscal conferir                 # internal consistency, reported not fixed
python -m fiscal exportar                 # the snapshot the panel reads
```

## The panel

```bash
cd painel && npm install && npm run build && npx serve out
```

A Next.js static export: the page is generated at build time from the snapshot
on disk, so the visitor downloads HTML with the numbers already in it. No
backend, no database in production, no loading state. One client component
exists, for searching and sorting 1,793 rows.

## Test it

```bash
python -m unittest discover -s tests -t .
```

**49 tests, no network and no real waiting** — the HTTP transport and the clock
are both injected. The fixtures are responses **captured from the live API** on
2026-09-03, including the empty one, because the empty response is this API's
central trap.

Among them is a **cross-language contract test**: it reads the TypeScript types
as text and compares them against what the Python exporter actually writes. The
snapshot is positional, so swapping two columns in Python breaks nothing
anywhere — it just starts showing population where the percentage should be.
That test is the only thing standing between such a swap and a silently wrong
page. It was verified by deliberately swapping two columns and watching it
fail.

## Three things it gets right on purpose

**An empty answer is not an error, and it is not zero.** `/rgf` without
`id_ente` returns `items: []` with HTTP **200**. A client that treats that as a
failure retries forever; one that treats it as data records zero where there is
no information. Here it is recorded as an explicit absence, distinct both from
"filed a zero" and from "not asked yet".

**The sweep assumes it will be interrupted.** An hour of network is plenty of
time for something to go wrong, so each municipality is written and marked as it
arrives. A network failure becomes a status code, never an exception — a socket
`TimeoutError` is an `OSError`, not a `URLError`, a distinction that once took
down a sibling project mid-run.

**The percentage is never recalculated.** It arrives computed and filed by the
municipality itself. Recomputing it from `spending / revenue` would invent a
second truth that nobody signed.

## The check that found its own author's mistake

`fiscal conferir` recomputes `spending / adjusted net revenue` and compares it
against the percentage the municipality filed — reporting divergence, never
correcting it. On its first run it diverged in **every single municipality**,
always in the same direction.

That was not noise. The filed percentage is computed over the **adjusted** net
current revenue, and the check was using the gross figure. Salvador's 2024 Q3
report carries both: 10,384,311,525.53 gross against 10,250,806,767.53 adjusted,
and 3,318,008,507.92 over the adjusted figure is exactly the 32.37% filed.
**Divergence in one direction only is never chance.** After the fix: zero
divergences.

## What it does not do

It does not interpret, accuse, or declare anyone in breach — it shows the filed
figure and the legal limit. It has no backend, no production database, and no
login.

## First real numbers

From the live API on 2026-09-03, for 2024's third quarter, in the first 25
municipalities of Maranhão by IBGE code:

- **9 of 25 had filed at all.** A spot check of one non-filer returned empty
  across four different periods and every parameter combination, while a control
  query for Salvador returned 225 rows — the absence is real, not a query
  artifact.
- **4 of those 9 were over the 51.3% prudential threshold**, and
  **Alto Parnaíba/MA at 57.52% was over the 54% legal cap itself.**

That ratio comes from one state's alphabetical head, not a regional sample.
Establishing the real figure for all 1,793 is what the full sweep is for.

## License

AGPL-3.0.
