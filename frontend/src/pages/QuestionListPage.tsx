/**
 * Question Bank list screen (UC-02 §6, §23).
 *
 * Shows the metadata the requirement calls for, supports search/filtering, and exposes the
 * lifecycle actions. Delete is offered only where it is actually legal — a question with attempt
 * history shows Retire instead, matching the backend rule rather than discovering it via a 409.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { ApiError, api, type QuestionFilters } from '../api/client';
import {
  QUESTION_STATUSES,
  QUESTION_TYPES,
  QUESTION_TYPE_LABELS,
  type PageMeta,
  type QuestionListItem,
} from '../api/types';
import {
  ErrorSummary,
  Modal,
  Spinner,
  StatusBadge,
  TypeBadge,
  formatDate,
  truncate,
  useToast,
} from '../components/ui';

const PAGE_SIZE = 20;

export function QuestionListPage(): ReactNode {
  const navigate = useNavigate();
  const toast = useToast();

  const [items, setItems] = useState<QuestionListItem[]>([]);
  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const [search, setSearch] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [deliverableOnly, setDeliverableOnly] = useState(false);
  const [page, setPage] = useState(1);

  const [retiring, setRetiring] = useState<QuestionListItem | null>(null);
  const [retireReason, setRetireReason] = useState('');
  const [deleting, setDeleting] = useState<QuestionListItem | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const filters: QuestionFilters = {
      page,
      pageSize: PAGE_SIZE,
      sortBy: 'createdAt',
      sortDir: 'desc',
    };
    if (search.trim()) filters.search = search.trim();
    if (type) filters.type = [type];
    if (status) filters.status = [status];
    if (deliverableOnly) filters.deliverableOnly = true;

    try {
      const result = await api.listQuestions(filters);
      setItems(result.items);
      setMeta(result.meta);
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }, [page, search, type, status, deliverableOnly]);

  useEffect(() => {
    // Debounce so typing in the search box does not fire a request per keystroke.
    const timer = window.setTimeout(load, search ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [load, search]);

  async function confirmRetire(): Promise<void> {
    if (!retiring) return;
    setBusy(true);
    try {
      await api.retireQuestion(retiring.id, retireReason.trim());
      toast.success(
        `${retiring.reference} retired. It is withheld from future quizzes and remains available for reporting.`,
      );
      setRetiring(null);
      setRetireReason('');
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The question could not be retired.');
    } finally {
      setBusy(false);
    }
  }

  async function reactivate(item: QuestionListItem): Promise<void> {
    try {
      await api.reactivateQuestion(item.id);
      toast.success(`${item.reference} is active again and eligible for delivery.`);
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The question could not be reactivated.');
    }
  }

  async function confirmDelete(): Promise<void> {
    if (!deleting) return;
    setBusy(true);
    try {
      const result = await api.deleteQuestion(deleting.id);
      toast.success(result.message);
      setDeleting(null);
      await load();
    } catch (cause) {
      // Most likely QUESTION_HAS_HISTORY — show the backend's explanation verbatim.
      toast.error(cause instanceof ApiError ? cause.message : 'The question could not be deleted.');
      setDeleting(null);
    } finally {
      setBusy(false);
    }
  }

  function resetFilters(): void {
    setSearch('');
    setType('');
    setStatus('');
    setDeliverableOnly(false);
    setPage(1);
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Question Bank</h1>
          <p>
            Create, edit, tag and retire questions. Retiring withdraws a question from future
            quizzes while keeping every completed attempt fully reportable.
          </p>
        </div>
        <div className="row tight">
          <Link className="btn" to="/import">
            CSV import
          </Link>
          <Link className="btn btn-primary" to="/questions/new">
            + New question
          </Link>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="row tight" style={{ flex: 1 }}>
            <input
              type="search"
              value={search}
              placeholder="Search text, scenario or reference (Q-000001)"
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
              style={{ maxWidth: 340 }}
            />
            <select
              value={type}
              onChange={(event) => {
                setType(event.target.value);
                setPage(1);
              }}
              style={{ maxWidth: 190 }}
              aria-label="Filter by type"
            >
              <option value="">All types</option>
              {QUESTION_TYPES.map((value) => (
                <option key={value} value={value}>
                  {QUESTION_TYPE_LABELS[value]}
                </option>
              ))}
            </select>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
              style={{ maxWidth: 150 }}
              aria-label="Filter by status"
            >
              <option value="">All statuses</option>
              {QUESTION_STATUSES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            <label className="row tight" style={{ whiteSpace: 'nowrap' }}>
              <input
                type="checkbox"
                checked={deliverableOnly}
                onChange={(event) => {
                  setDeliverableOnly(event.target.checked);
                  setPage(1);
                }}
                style={{ width: 'auto' }}
              />
              <span className="subtle">Deliverable only</span>
            </label>
            {(search || type || status || deliverableOnly) && (
              <button type="button" className="btn btn-sm btn-ghost" onClick={resetFilters}>
                Clear
              </button>
            )}
          </div>
          <span className="subtle">{meta ? `${meta.total} question(s)` : ''}</span>
        </div>

        {error ? (
          <div className="card-body">
            <ErrorSummary error={error} />
          </div>
        ) : loading ? (
          <div className="empty">
            <Spinner label="Loading questions…" />
          </div>
        ) : items.length === 0 ? (
          <div className="empty">
            No questions match these filters.{' '}
            <Link to="/questions/new">Create the first one</Link> or{' '}
            <Link to="/import">import a CSV</Link>.
          </div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Reference</th>
                    <th>Type</th>
                    <th className="cell-main">Question</th>
                    <th>Topics</th>
                    <th>Status</th>
                    <th>Scoring</th>
                    <th>Created</th>
                    <th>Updated</th>
                    <th className="cell-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <Link className="mono" to={`/questions/${item.id}`}>
                          {item.reference}
                        </Link>
                        {item.version > 1 && <div className="cell-sub">v{item.version}</div>}
                      </td>
                      <td>
                        <TypeBadge type={item.type} />
                      </td>
                      <td className="cell-main">
                        <div className="cell-title">{truncate(item.questionText)}</div>
                        <div className="cell-sub">
                          {item.optionCount} option{item.optionCount === 1 ? '' : 's'}
                          {item.usageCount > 0
                            ? ` · used by ${item.usageCount} attempt${item.usageCount === 1 ? '' : 's'}`
                            : ''}
                          {item.difficulty ? ` · ${item.difficulty}` : ''}
                        </div>
                      </td>
                      <td>
                        <div className="row tight">
                          {item.topics.map((topic) => (
                            <span className="tag tag-static" key={topic.id}>
                              {topic.name}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="subtle">
                        {item.points} pt{item.points === 1 ? '' : 's'}
                        <div className="cell-sub">{item.scoringStrategy}</div>
                      </td>
                      <td className="subtle">{formatDate(item.createdAt)}</td>
                      <td className="subtle">{formatDate(item.updatedAt)}</td>
                      <td className="cell-actions">
                        <div className="row tight" style={{ justifyContent: 'flex-end' }}>
                          <button
                            type="button"
                            className="btn btn-sm"
                            onClick={() => navigate(`/questions/${item.id}`)}
                          >
                            {item.status === 'RETIRED' ? 'View' : 'Edit'}
                          </button>
                          {item.status === 'RETIRED' ? (
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => reactivate(item)}
                            >
                              Reactivate
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-sm"
                              onClick={() => {
                                setRetiring(item);
                                setRetireReason('');
                              }}
                            >
                              Retire
                            </button>
                          )}
                          {/* Delete is only shown when the question has no history at all. */}
                          {item.usageCount === 0 && (
                            <button
                              type="button"
                              className="btn btn-sm btn-ghost"
                              style={{ color: 'var(--c-danger)' }}
                              onClick={() => setDeleting(item)}
                            >
                              Delete
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {meta && meta.totalPages > 1 && (
              <div className="pagination">
                <span className="subtle">
                  Page {meta.page} of {meta.totalPages}
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={!meta.hasPrevious}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                >
                  Previous
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={!meta.hasNext}
                  onClick={() => setPage((current) => current + 1)}
                >
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </div>

      {retiring && (
        <Modal
          title={`Retire ${retiring.reference}?`}
          onClose={() => setRetiring(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setRetiring(null)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={confirmRetire} disabled={busy}>
                {busy ? 'Retiring…' : 'Retire question'}
              </button>
            </>
          }
        >
          <div className="alert alert-info" style={{ marginBottom: 16 }}>
            <strong>Retiring is reversible and preserves all history.</strong>
            The question keeps its identity ({retiring.reference}), stays out of every future quiz,
            and remains fully available to reports for the{' '}
            {retiring.usageCount === 0 ? 'attempts that may already reference it' : `${retiring.usageCount} attempt(s) that used it`}.
          </div>
          <p className="pre-wrap" style={{ marginTop: 0 }}>
            {truncate(retiring.questionText, 220)}
          </p>
          <label className="field-label" htmlFor="retire-reason">
            Reason (optional)
          </label>
          <textarea
            id="retire-reason"
            value={retireReason}
            placeholder="e.g. Superseded by the 2026 syllabus"
            onChange={(event) => setRetireReason(event.target.value)}
          />
        </Modal>
      )}

      {deleting && (
        <Modal
          title={`Permanently delete ${deleting.reference}?`}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setDeleting(null)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={confirmDelete} disabled={busy}>
                {busy ? 'Deleting…' : 'Delete permanently'}
              </button>
            </>
          }
        >
          <div className="alert alert-warning" style={{ marginBottom: 16 }}>
            <strong>This cannot be undone.</strong>
            This question has never been delivered to an attempt, so deleting it destroys no
            history. If it is ever used, delete will be refused and you will need to retire it
            instead.
          </div>
          <p className="pre-wrap" style={{ margin: 0 }}>
            {truncate(deleting.questionText, 220)}
          </p>
        </Modal>
      )}
    </div>
  );
}
