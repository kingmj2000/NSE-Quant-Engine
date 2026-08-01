import { createFileRoute } from "@tanstack/react-router";
import { Activity, AlertTriangle, Github, LineChart, ShieldCheck, Terminal } from "lucide-react";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "NSE Quant Engine — Offline NSE Screener & Validation Lab" },
      {
        name: "description",
        content:
          "Open-source desktop screener for NSE equities and ETFs: deterministic scoring, cost-aware out-of-sample validation, watchlist-only by default.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { property: "og:title", content: "NSE Quant Engine" },
      {
        property: "og:description",
        content:
          "A local NSE screener that tells you honestly when it has no proven edge. Python computes, validation measures, the human decides.",
      },
    ],
  }),
  component: Index,
});

const pillars = [
  {
    icon: LineChart,
    title: "Python computes",
    body: "Every score, rank, factor and cost estimate comes from deterministic Python. No model guesses a number.",
  },
  {
    icon: ShieldCheck,
    title: "Validation measures",
    body: "One question: does the ranking beat the benchmark after costs, out of sample? Walk-forward, shrunk, cost-aware.",
  },
  {
    icon: Terminal,
    title: "AI explains",
    body: "An optional evidence bundle lets an LLM narrate what the numbers already say. Explanation never feeds back into scores.",
  },
  {
    icon: Activity,
    title: "The human decides",
    body: "No broker integration, no orders. A screener with an audit trail — and a loud watchlist-only state.",
  },
];

const outputs = [
  ["validation_status.json", "Canonical, authoritative verdict"],
  ["nse_quant_scores.xlsx", "Official scores and ranks"],
  ["daily_changes.json", "Entrants, exits, rank gainers, new risk flags"],
  ["news_digest.json", "Context-only news and exchange filings"],
  ["dashboard_latest.html", "Dashboard, embedded in the desktop app"],
];

function Index() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <section className="mx-auto max-w-5xl px-6 pt-20 pb-14">
        <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-border px-3 py-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          <span className="inline-block size-2 rounded-full bg-destructive" />
          Watchlist-only until validated
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">NSE Quant Engine</h1>
        <p className="mt-5 max-w-2xl text-lg text-muted-foreground">
          A local, offline-first screener and analytics engine for NSE equities and ETFs, with a
          PySide6 desktop interface. This page is only the project landing page — the engine itself
          runs on your machine.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          <a
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
            href="https://github.com/"
          >
            <Github className="size-4" aria-hidden="true" />
            View the repository
          </a>
          <a
            className="inline-flex items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium transition-colors hover:bg-muted"
            href="#install"
          >
            Installation
          </a>
        </div>

        <div className="mt-10 rounded-lg border border-destructive/40 bg-destructive/5 p-5">
          <p className="flex items-start gap-3 text-sm leading-relaxed">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden="true" />
            <span>
              <strong>Honest limitation.</strong> This is a screener that, once validated, can tell
              you whether its ranking beats a benchmark after costs. It is not a low-risk,
              high-profit oracle. Its most valuable possible answer is often{" "}
              <em>&ldquo;No Proven Edge Yet.&rdquo;</em> Research and educational use only — not
              investment advice.
            </span>
          </p>
        </div>
      </section>

      <section className="border-t border-border bg-muted/30">
        <div className="mx-auto max-w-5xl px-6 py-14">
          <h2 className="text-2xl font-semibold tracking-tight">Architecture in one line</h2>
          <p className="mt-2 text-muted-foreground">
            Python computes. Validation measures. AI explains. The human decides.
          </p>
          <div className="mt-8 grid gap-5 sm:grid-cols-2">
            {pillars.map(({ icon: Icon, title, body }) => (
              <div key={title} className="rounded-lg border border-border bg-background p-5">
                <Icon className="size-5 text-primary" aria-hidden="true" />
                <h3 className="mt-3 font-medium">{title}</h3>
                <p className="mt-1.5 text-sm text-muted-foreground">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-14">
        <h2 className="text-2xl font-semibold tracking-tight">Validation philosophy</h2>
        <ul className="mt-5 space-y-3 text-sm text-muted-foreground">
          <li>
            <strong className="text-foreground">One source of truth.</strong>{" "}
            <code className="rounded bg-muted px-1.5 py-0.5">validation_status.json</code> holds the
            verdict. Reports are never scraped for it.
          </li>
          <li>
            <strong className="text-foreground">One ranking authority.</strong> Confidence-adjusted
            score descending, symbol ascending. Raw score rank is diagnostic only.
          </li>
          <li>
            <strong className="text-foreground">Evidence, not vibes.</strong> Walk-forward
            evaluation, an incremental residual-IC gate for new alphas, Bayesian shrinkage on IC and
            hit rate, costs included.
          </li>
          <li>
            <strong className="text-foreground">Context stays context.</strong> News and filings are
            shown for human review and never touch scores or ranks.
          </li>
          <li>
            <strong className="text-foreground">Dormant by default.</strong> Adaptive weighting
            ships disabled and must be enabled deliberately.
          </li>
        </ul>
      </section>

      <section className="border-t border-border">
        <div className="mx-auto max-w-5xl px-6 py-14">
          <h2 className="text-2xl font-semibold tracking-tight">Desktop application</h2>
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <div
              className="flex min-h-56 items-center justify-center rounded-lg border border-dashed border-border bg-muted/40 p-6 text-center text-sm text-muted-foreground"
              role="img"
              aria-label="Placeholder for a desktop application screenshot"
            >
              Screenshot placeholder — Decision Center, Candidates Workbench and Data Health panels.
              Screenshots are omitted until a portfolio-redacted capture is available.
            </div>
            <div id="install">
              <h3 className="font-medium">Install and run</h3>
              <pre className="mt-3 overflow-x-auto rounded-lg border border-border bg-muted/50 p-4 text-xs leading-relaxed">
                <code>{`cd desktop/nse_quant_engine
python -m venv .venv
pip install -r requirements.txt
python run_app.py

# headless full pipeline
python orchestrator.py --all`}</code>
              </pre>
              <p className="mt-3 text-sm text-muted-foreground">
                Requires Python 3.11 or 3.12. Runtime caches and results are written locally and
                never published.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-border bg-muted/30">
        <div className="mx-auto max-w-5xl px-6 py-14">
          <h2 className="text-2xl font-semibold tracking-tight">What a run produces</h2>
          <dl className="mt-6 divide-y divide-border overflow-hidden rounded-lg border border-border bg-background">
            {outputs.map(([file, desc]) => (
              <div
                key={file}
                className="flex flex-col gap-1 p-4 sm:flex-row sm:items-center sm:gap-6"
              >
                <dt className="font-mono text-sm sm:w-72">{file}</dt>
                <dd className="text-sm text-muted-foreground">{desc}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-sm text-muted-foreground">
            Synthetic examples of these shapes live in{" "}
            <code className="rounded bg-muted px-1.5 py-0.5">examples/sample_output/</code>. No real
            market data, validation history or portfolio file is published.
          </p>
        </div>
      </section>

      <footer className="border-t border-border">
        <div className="mx-auto max-w-5xl px-6 py-10 text-sm text-muted-foreground">
          <p>
            MIT licensed. Provided for research and educational use.{" "}
            <strong className="text-foreground">Not investment advice.</strong> Markets carry risk;
            past performance does not guarantee future results.
          </p>
        </div>
      </footer>
    </main>
  );
}
