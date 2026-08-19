/**
 * CSV bulk import screen (UC-02 §23).
 *
 *   Select CSV -> Upload -> Parse -> Validate every row -> Import valid rows -> Report rejects
 *
 * The result view leads with the imported/rejected counts and then lists every rejected row with
 * its row number and reasons, which is what makes a partial import actionable.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type { ImportListItem, ImportResult, TemplateGuide } from '../api/types';
import { ErrorSummary, Spinner, formatDate, truncate, useToast } from '../components/ui';

type Phase = 'idle' | 'uploading' | 'done';

export function ImportPage(): ReactNode {
  const toast = useToast();
  const fileInput = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [guide, setGuide] = useState<TemplateGuide | null>(null);
  const [history, setHistory] = useState<ImportListItem[]>([]);
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    api.templateGuide().then(setGuide).catch(() => setGuide(null));
    refreshHistory();
  }, []);

  function refreshHistory(): void {
    api
      .listImports()
      .then((response) => setHistory(response.items))
      .catch(() => setHistory([]));
  }

  async function upload(): Promise<void> {
    if (!file) return;
    setPhase('uploading');
    setError(null);
    setResult(null);
    try {
      const outcome = await api.importCsv(file);
      setResult(outcome);
      setPhase('done');
      if (outcome.rejectedRows === 0) {
        toast.success(`Imported all ${outcome.importedRows} row(s).`);
      } else if (outcome.importedRows === 0) {
        toast.error(`No rows imported — all ${outcome.rejectedRows} row(s) were rejected.`);
      } else {
        toast.info(
          `Imported ${outcome.importedRows} row(s); rejected ${outcome.rejectedRows}. See the report below.`,
        );
      }
      refreshHistory();
    } catch (cause) {
      // A whole-file failure (missing headers, unreadable file) lands here — nothing imported.
      setError(cause);
      setPhase('idle');
      toast.error(cause instanceof ApiError ? cause.message : 'The file could not be imported.');
      refreshHistory();
    }
  }

  function reset(): void {
    setFile(null);
    setResult(null);
    setError(null);
    setPhase('idle');
    if (fileInput.current) fileInput.current.value = '';
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>CSV bulk import</h1>
          <p>
            Every row is validated independently. Valid rows are imported even if others fail, and
            each rejected row is reported with its row number and the exact reason.
          </p>
        </div>
        <div className="row tight">
          <a className="btn" href={api.templateUrl()} download>
            Download template
          </a>
          <button type="button" className="btn" onClick={() => setShowGuide((value) => !value)}>
            {showGuide ? 'Hide format guide' : 'Format guide'}
          </button>
          <Link className="btn" to="/questions">
            Question bank
          </Link>
        </div>
      </div>

      {showGuide && guide && (
        <div className="card">
          <div className="card-header">
            <h2>CSV format</h2>
            <span className="subtle">
              Repeated values use <code>{guide.listDelimiter}</code> · max {guide.maxRows} rows
            </span>
          </div>
          <div className="card-body">
            <p style={{ marginTop: 0 }}>
              <strong>Options syntax:</strong> <code>{guide.optionSyntax}</code>
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Required</th>
                    <th className="cell-main">Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {guide.fields.map((field) => (
                    <tr key={field.column}>
                      <td className="mono">{field.column}</td>
                      <td className="subtle">{field.required}</td>
                      <td className="cell-main">{field.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="field-hint" style={{ marginTop: 12 }}>
              The downloadable template contains one worked example per question type.
            </p>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h2>Upload</h2>
        </div>
        <div className="card-body">
          <div className="field">
            <label htmlFor="csv-file">CSV file</label>
            <input
              id="csv-file"
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              disabled={phase === 'uploading'}
              onChange={(event) => {
                setFile(event.target.files?.[0] ?? null);
                setResult(null);
                setError(null);
                setPhase('idle');
              }}
            />
            {file && (
              <div className="field-hint">
                {file.name} · {(file.size / 1024).toFixed(1)} KB
              </div>
            )}
          </div>

          <div className="row tight" style={{ marginTop: 16 }}>
            <button
              type="button"
              className="btn btn-primary"
              onClick={upload}
              disabled={!file || phase === 'uploading'}
            >
              {phase === 'uploading' ? 'Importing…' : 'Parse, validate and import'}
            </button>
            {(result !== null || error !== null) && (
              <button type="button" className="btn" onClick={reset}>
                Import another file
              </button>
            )}
            {phase === 'uploading' && <Spinner label="Validating every row…" />}
          </div>

          {error ? (
            <div style={{ marginTop: 16 }}>
              <ErrorSummary error={error} />
              <p className="field-hint">
                Nothing was imported — this problem affects the whole file, not an individual row.
              </p>
            </div>
          ) : null}
        </div>
      </div>

      {result && (
        <div className="card">
          <div className="card-header">
            <h2>Import result</h2>
            <span className="subtle">
              {result.filename} · {formatDate(result.startedAt)} ·{' '}
              <span className="mono">{result.id}</span>
            </span>
          </div>
          <div className="card-body">
            <div className="grid-3">
              <div className="stat">
                <div className="stat-value">{result.totalRows}</div>
                <div className="stat-label">Total rows</div>
              </div>
              <div className="stat stat-imported">
                <div className="stat-value">{result.importedRows}</div>
                <div className="stat-label">Imported</div>
              </div>
              <div className="stat stat-rejected">
                <div className="stat-value">{result.rejectedRows}</div>
                <div className="stat-label">Rejected</div>
              </div>
            </div>

            {result.rejectedRows === 0 ? (
              <div className="alert alert-success" style={{ marginTop: 16 }}>
                <strong>Every row was valid and imported.</strong>
                {result.importedRows} question(s) are now in the bank.
              </div>
            ) : result.importedRows === 0 ? (
              <div className="alert alert-error" style={{ marginTop: 16 }}>
                <strong>No rows could be imported.</strong>
                Fix the problems listed below and upload the file again.
              </div>
            ) : (
              <div className="alert alert-warning" style={{ marginTop: 16 }}>
                <strong>Partial import.</strong>
                The {result.importedRows} valid row(s) were saved. The {result.rejectedRows} rejected
                row(s) below were not — correct them and re-upload just those rows.
              </div>
            )}
          </div>

          {result.rejected.length > 0 && (
            <>
              <div className="card-header" style={{ borderTop: '1px solid var(--c-border)' }}>
                <h3>Rejected rows</h3>
                <span className="subtle">Row numbers match your spreadsheet (header is row 1)</span>
              </div>
              <div className="card-body tight">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Row</th>
                        <th>Field</th>
                        <th className="cell-main">Problem</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.rejected.flatMap((row) =>
                        row.errors.map((issue, index) => (
                          <tr key={`${row.rowNumber}-${issue.code}-${index}`}>
                            {index === 0 ? (
                              <td rowSpan={row.errors.length} className="mono">
                                {row.rowNumber}
                                {row.rawRow?.question_text || row.rawRow?.Question ? (
                                  <div className="cell-sub">
                                    {truncate(
                                      row.rawRow.question_text ?? row.rawRow.Question ?? '',
                                      40,
                                    )}
                                  </div>
                                ) : null}
                              </td>
                            ) : null}
                            <td className="mono subtle">{issue.field ?? '—'}</td>
                            <td className="cell-main">
                              {issue.message}
                              <div className="cell-sub mono">{issue.code}</div>
                            </td>
                          </tr>
                        )),
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}

          {result.imported.length > 0 && (
            <>
              <div className="card-header" style={{ borderTop: '1px solid var(--c-border)' }}>
                <h3>Imported questions</h3>
              </div>
              <div className="card-body tight">
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Row</th>
                        <th>Reference</th>
                        <th className="cell-main">Question</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.imported.map((row) => (
                        <tr key={row.questionId}>
                          <td className="mono">{row.rowNumber}</td>
                          <td>
                            <Link className="mono" to={`/questions/${row.questionId}`}>
                              {row.reference}
                            </Link>
                          </td>
                          <td className="cell-main">{truncate(row.questionText)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {history.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h2>Previous imports</h2>
          </div>
          <div className="card-body tight">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Status</th>
                    <th>Total</th>
                    <th>Imported</th>
                    <th>Rejected</th>
                    <th>Started</th>
                    <th className="cell-actions" />
                  </tr>
                </thead>
                <tbody>
                  {history.map((run) => (
                    <tr key={run.id}>
                      <td>{run.filename}</td>
                      <td>
                        <span
                          className={`badge ${run.status === 'FAILED' ? 'badge-retired' : 'badge-active'}`}
                        >
                          {run.status}
                        </span>
                        {run.errorMessage && <div className="cell-sub">{run.errorMessage}</div>}
                      </td>
                      <td className="subtle">{run.totalRows}</td>
                      <td className="subtle">{run.importedRows}</td>
                      <td className="subtle">{run.rejectedRows}</td>
                      <td className="subtle">{formatDate(run.startedAt)}</td>
                      <td className="cell-actions">
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={async () => {
                            try {
                              setResult(await api.getImport(run.id));
                              setError(null);
                              setPhase('done');
                              window.scrollTo({ top: 0, behavior: 'smooth' });
                            } catch (cause) {
                              toast.error(
                                cause instanceof ApiError ? cause.message : 'Could not load report.',
                              );
                            }
                          }}
                        >
                          View report
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
