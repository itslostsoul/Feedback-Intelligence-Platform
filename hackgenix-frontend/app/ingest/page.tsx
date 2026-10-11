"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";
import {
  API_BASE_URL,
  api,
  describeApiError,
  type BulkIngestResponse,
  type SyncResultResponse,
  type SyncSource,
  type WebhookIngestResponse,
  type WebhookSource,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/PageHeader";

type BulkFormat = "csv" | "json";

const JSON_EXAMPLE = JSON.stringify(
  [
    {
      external_id: "review_001",
      source: "app_store",
      source_type: "review",
      customer_id: "C101",
      customer_tier: "premium",
      raw_text: "My international card keeps getting declined.",
      created_at: "2026-09-25T10:30:00Z",
    },
  ],
  null,
  2
);

const WEBHOOK_SOURCES: { value: WebhookSource; label: string; example: string }[] = [
  {
    value: "generic",
    label: "Generic",
    example: JSON.stringify(
      {
        event_id: "evt_demo_001",
        customer: { id: "C100" },
        text: "Checkout keeps failing.",
        timestamp: "2026-09-25T10:00:00Z",
      },
      null,
      2
    ),
  },
  {
    value: "zendesk",
    label: "Zendesk",
    example: JSON.stringify(
      {
        id: 91827,
        requester: { id: "C1029", tier: "premium" },
        subject: "Card declined",
        description: "My international card keeps getting declined.",
        created_at: "2026-09-25T10:30:00Z",
      },
      null,
      2
    ),
  },
  {
    value: "survey",
    label: "Survey",
    example: JSON.stringify(
      {
        response_id: "resp_4471",
        respondent_id: "C2044",
        answer_text: "Onboarding was confusing, took three tries.",
        submitted_at: "2026-09-20T09:15:00Z",
      },
      null,
      2
    ),
  },
  {
    value: "app_store",
    label: "App Store",
    example: JSON.stringify(
      {
        review_id: "rev_7788",
        author: "throwaway_user92",
        rating: 2,
        review_text: "App crashes every time I open the camera.",
        date: "2026-09-18T00:00:00Z",
      },
      null,
      2
    ),
  },
];

const SYNC_SOURCES: { value: SyncSource; label: string; description: string }[] = [
  {
    value: "demo",
    label: "Demo partner API",
    description: "Simulates polling a partner's REST API that has no webhook support.",
  },
  {
    value: "csv_drop",
    label: "Demo CSV drop",
    description: "Simulates polling a shared CSV export location.",
  },
];

const codeBlockClass =
  "overflow-x-auto border border-border bg-surface-2 p-4 font-mono text-xs leading-relaxed text-foreground";
const selectClass =
  "border-b border-border bg-transparent px-0 py-1 text-sm text-foreground outline-none transition-colors hover:text-brass focus-visible:border-brass";

export default function IngestPage() {
  return (
    <main className="mx-auto max-w-[1180px] px-6 py-12 md:px-12 md:py-16">
      <PageHeader
        eyebrow="SignalForge"
        title="Ingest Feedback"
        description="A company should never need to type thousands of feedback records by hand. Five channels feed the same common ingestion service — deduplicated the same way, and processed by the same AI pipeline, no matter how a record arrived."
      />

      <div className="mt-4 space-y-16">
        <BulkFileSection />
        <RestSection />
        <WebhookSection />
        <SyncSection />
      </div>
    </main>
  );
}

function SectionHeading({ index, title, description }: { index: string; title: string; description: string }) {
  return (
    <div className="mb-6 border-b border-border pb-4">
      <div className="mb-1.5 flex items-baseline gap-2.5">
        <span className="font-mono text-xs tabular-nums text-muted-foreground">{index}</span>
        <h2 className="font-serif text-2xl tracking-tight text-foreground">{title}</h2>
      </div>
      <p className="max-w-[70ch] text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

function StatTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning" | "success" | "muted";
}) {
  const toneClass = tone === "warning" ? "text-warning" : tone === "success" ? "text-chart-2" : "text-foreground";
  return (
    <div className="border-l border-border pl-3">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={`font-serif text-3xl ${toneClass}`}>{value.toLocaleString()}</p>
    </div>
  );
}

function BulkStats({ result }: { result: BulkIngestResponse }) {
  return (
    <>
      <div className="mt-6 grid grid-cols-2 gap-6 border-t border-border pt-6 md:grid-cols-4">
        <StatTile label="Records found" value={result.total_rows} />
        <StatTile label="Accepted" value={result.accepted} tone="success" />
        <StatTile label="Duplicates" value={result.duplicates} />
        <StatTile label="Rejected" value={result.rejected} tone={result.rejected > 0 ? "warning" : "muted"} />
      </div>
      {result.errors.length > 0 && (
        <div className="mt-4 space-y-1">
          {result.errors.slice(0, 10).map((e) => (
            <p key={e.row} className="font-mono text-xs text-warning">
              Row {e.row}: {e.message}
            </p>
          ))}
          {result.errors.length > 10 && (
            <p className="text-xs text-muted-foreground">…and {result.errors.length - 10} more.</p>
          )}
        </div>
      )}
    </>
  );
}

/** Copies to the clipboard and shows a brief "Copied" confirmation — never
 * claims success without a real clipboard write succeeding. */
function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access denied/unavailable — nothing to fall back to silently.
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="inline-flex items-center gap-1.5 border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:border-brass hover:text-brass"
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

function BulkFileSection() {
  const [format, setFormat] = useState<BulkFormat>("csv");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<BulkIngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  function switchFormat(next: BulkFormat) {
    setFormat(next);
    setFile(null);
    setResult(null);
    setError(null);
  }

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const response = format === "csv" ? await api.feedback.bulkIngest(file) : await api.feedback.bulkIngestJson(file);
      setResult(response);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <section>
      <SectionHeading
        index="01"
        title="Bulk file upload"
        description="A company that already has historical feedback — an export from a spreadsheet, ticketing system, or data warehouse — uploads it here in one file instead of typing thousands of records by hand. CSV and JSON both flow through the same common ingestion service."
      />

      <div className="mb-6 flex gap-2">
        {(["csv", "json"] as const).map((f) => (
          <button
            key={f}
            onClick={() => switchFormat(f)}
            className={`border px-4 py-1.5 text-xs uppercase tracking-wide transition-colors ${
              format === f ? "border-brass text-brass" : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {format === "csv" ? (
        <p className="mb-4 text-sm text-muted-foreground">
          Required columns: <span className="font-mono text-xs">source</span>,{" "}
          <span className="font-mono text-xs">raw_text</span>. Optional:{" "}
          <span className="font-mono text-xs">external_id, source_type, customer_id, customer_tier, language, created_at</span>.
        </p>
      ) : (
        <div className="mb-4">
          <p className="mb-3 text-sm text-muted-foreground">Expected format: a JSON array of feedback records.</p>
          <div className="flex items-start gap-3">
            <pre className={`${codeBlockClass} flex-1`}>{JSON_EXAMPLE}</pre>
            <CopyButton text={JSON_EXAMPLE} />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <input
          type="file"
          accept={format === "csv" ? ".csv,text/csv" : ".json,application/json"}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-sm text-muted-foreground file:mr-4 file:border file:border-border file:bg-surface-2 file:px-3 file:py-1.5 file:text-xs file:text-foreground"
        />
        <Button onClick={handleUpload} disabled={!file || uploading}>
          {uploading ? "Uploading…" : "Upload & Ingest"}
        </Button>
      </div>
      {error && <p className="mt-4 text-xs text-destructive">{error}</p>}
      {result && <BulkStats result={result} />}
    </section>
  );
}

function RestSection() {
  const example = `POST /api/v1/feedback
Content-Type: application/json

{
  "external_id": "ticket_91827",
  "source": "zendesk",
  "source_type": "support_ticket",
  "customer_id": "C1029",
  "customer_tier": "premium",
  "raw_text": "My international card keeps getting declined.",
  "created_at": "2026-09-25T10:30:00Z"
}`;

  return (
    <section>
      <SectionHeading
        index="02"
        title="REST API"
        description="A company's own application or backend can push feedback in directly, as it happens — the same endpoint used by the quick single-item form on the Raw Firehose page."
      />
      <div className="flex items-start gap-3">
        <pre className={`${codeBlockClass} flex-1`}>{example}</pre>
        <CopyButton text={example} />
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Returns <span className="font-mono">201 Created</span> for a new (source, external_id) pair, or{" "}
        <span className="font-mono">200 OK</span> with the existing record if that pair was already ingested.
      </p>
    </section>
  );
}

function FlowStep({ label, muted }: { label: string; muted?: boolean }) {
  return <p className={muted ? "text-muted-foreground" : "font-medium text-foreground"}>{label}</p>;
}

function WebhookFlowDiagram() {
  const steps = ["External Platform", "Webhook Event", "SignalForge", "Normalize", "Common Ingestion Service", "PostgreSQL"];
  return (
    <div className="border border-border bg-surface-2 p-5 font-mono text-xs">
      {steps.map((step, i) => (
        <div key={step}>
          <FlowStep label={step} muted={i !== 2} />
          {i < steps.length - 1 && <p className="py-1 text-muted-foreground">↓</p>}
        </div>
      ))}
    </div>
  );
}

function WebhookSection() {
  const [source, setSource] = useState<WebhookSource>("generic");
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<WebhookIngestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selected = WEBHOOK_SOURCES.find((s) => s.value === source) ?? WEBHOOK_SOURCES[0];
  const endpointPath = `/api/v1/ingestion/webhook/${source}`;
  const endpointUrl = `${API_BASE_URL}${endpointPath}`;

  async function handleSendTestEvent() {
    setSending(true);
    setError(null);
    setResult(null);
    try {
      const payload = JSON.parse(selected.example) as Record<string, unknown>;
      // Sending the same example twice demonstrates dedup live: the second
      // call comes back "duplicate" against the same stored record instead
      // of creating a second one. This calls the real endpoint — nothing here
      // is simulated client-side.
      const response = await api.ingestion.webhook(source, payload);
      setResult(response);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setSending(false);
    }
  }

  const checklist = result
    ? [
        "Webhook received",
        "Payload normalized",
        result.status === "created" ? "Feedback ingested" : "Already ingested — duplicate detected, not re-created",
        "Deduplication checked",
      ]
    : [];

  return (
    <section>
      <SectionHeading
        index="03"
        title="Webhook ingestion"
        description="Not a file upload — connect an external system and automatically send new feedback events to SignalForge as they happen. Configure your support, survey, or review platform to POST new events to this endpoint; SignalForge normalizes provider-specific payloads before storing them."
      />

      <div className="grid gap-8 md:grid-cols-[1fr_260px]">
        <div>
          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Source</label>
          <select
            className={`${selectClass} mb-4`}
            value={source}
            onChange={(e) => {
              setSource(e.target.value as WebhookSource);
              setResult(null);
              setError(null);
            }}
          >
            {WEBHOOK_SOURCES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>

          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Webhook endpoint</label>
          <div className="mb-4 flex items-center gap-2">
            <code className="flex-1 overflow-x-auto border border-border bg-surface-2 px-3 py-2 text-xs text-foreground">
              POST {endpointUrl}
            </code>
            <CopyButton text={endpointUrl} label="Copy endpoint" />
          </div>

          <label className="mb-1.5 block text-xs font-medium text-muted-foreground">Example payload ({selected.label})</label>
          <div className="mb-4 flex items-start gap-2">
            <pre className={`${codeBlockClass} flex-1`}>{selected.example}</pre>
            <CopyButton text={selected.example} />
          </div>

          <Button variant="outline" size="sm" onClick={handleSendTestEvent} disabled={sending}>
            {sending ? "Sending…" : "Send Test Event"}
          </Button>
          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

          {result && (
            <div className="mt-5 space-y-1.5 border-t border-border pt-4">
              {checklist.map((line) => (
                <p key={line} className="flex items-center gap-2 text-sm text-chart-2">
                  <Check className="h-3.5 w-3.5 shrink-0" strokeWidth={2.5} />
                  {line}
                </p>
              ))}
              <p className="pt-1 font-mono text-xs text-muted-foreground">
                feedback id {result.feedback.id.slice(0, 8)} · status {result.status}
              </p>
            </div>
          )}
        </div>

        <div>
          <p className="mb-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">How it flows</p>
          <WebhookFlowDiagram />
        </div>
      </div>
    </section>
  );
}

function SyncSection() {
  const [source, setSource] = useState<SyncSource>("demo");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<SyncResultResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRunSync() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.ingestion.sync(source);
      setResult(response);
    } catch (err) {
      setError(describeApiError(err));
    } finally {
      setRunning(false);
    }
  }

  return (
    <section>
      <SectionHeading
        index="04"
        title="Scheduled sync"
        description="For systems that don't support webhooks at all — SignalForge periodically checks them for new feedback instead. This never runs automatically in the background; a real deployment would point an external cron job at this same endpoint."
      />
      <p className="mb-4 font-mono text-xs text-muted-foreground">POST /api/v1/ingestion/sync/{"{source}"}</p>
      <div className="mb-4 flex flex-wrap gap-3">
        {SYNC_SOURCES.map((s) => (
          <button
            key={s.value}
            onClick={() => {
              setSource(s.value);
              setResult(null);
              setError(null);
            }}
            className={`max-w-[16rem] border px-3 py-2 text-left text-xs transition-colors ${
              source === s.value ? "border-brass text-brass" : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            <p className="font-medium">{s.label}</p>
            <p className="mt-0.5 opacity-80">{s.description}</p>
          </button>
        ))}
      </div>
      <Button onClick={handleRunSync} disabled={running}>
        {running ? "Syncing…" : "Run sync now"}
      </Button>
      {error && <p className="mt-4 text-xs text-destructive">{error}</p>}
      {result && (
        <div className="mt-6 border-t border-border pt-6">
          <p className="mb-4 text-xs text-muted-foreground">
            Source: <span className="font-mono text-foreground">{result.source}</span>
          </p>
          <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
            <StatTile label="Records fetched" value={result.records_fetched} />
            <StatTile label="New" value={result.new} tone="success" />
            <StatTile label="Duplicates" value={result.duplicates} />
            <StatTile label="Failed" value={result.failed} tone={result.failed > 0 ? "warning" : "muted"} />
          </div>
        </div>
      )}
    </section>
  );
}
