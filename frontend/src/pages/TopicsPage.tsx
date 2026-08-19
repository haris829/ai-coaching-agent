/** Topic management screen (UC-02 §8, §23). */

import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type { Topic } from '../api/types';
import { ErrorSummary, Modal, Spinner, formatDate, useToast } from '../components/ui';

export function TopicsPage(): ReactNode {
  const toast = useToast();

  const [topics, setTopics] = useState<Topic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [search, setSearch] = useState('');

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [creating, setCreating] = useState(false);

  const [editing, setEditing] = useState<Topic | null>(null);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');

  const [deleting, setDeleting] = useState<Topic | null>(null);
  const [busy, setBusy] = useState(false);

  async function load(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      setTopics(await api.listTopics(search.trim() || undefined));
    } catch (cause) {
      setError(cause);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(load, search ? 250 : 0);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  async function create(): Promise<void> {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const topic = await api.createTopic({ name: name.trim(), description: description.trim() || null });
      toast.success(`Topic "${topic.name}" created.`);
      setName('');
      setDescription('');
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The topic could not be created.');
    } finally {
      setCreating(false);
    }
  }

  async function saveEdit(): Promise<void> {
    if (!editing) return;
    setBusy(true);
    try {
      await api.updateTopic(editing.id, {
        name: editName.trim(),
        description: editDescription.trim() || null,
      });
      toast.success('Topic updated.');
      setEditing(null);
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The topic could not be updated.');
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(topic: Topic): Promise<void> {
    try {
      await api.updateTopic(topic.id, { isActive: !topic.isActive });
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The topic could not be updated.');
    }
  }

  async function confirmDelete(force: boolean): Promise<void> {
    if (!deleting) return;
    setBusy(true);
    try {
      const response = await api.deleteTopic(deleting.id, force);
      toast.success(response.message);
      setDeleting(null);
      await load();
    } catch (cause) {
      toast.error(cause instanceof ApiError ? cause.message : 'The topic could not be deleted.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Topics</h1>
          <p>
            Topics are shared tags stored relationally, so renaming one updates every question that
            uses it. Historical reports are unaffected by topic changes — each attempt keeps the
            topic names frozen in its question snapshot.
          </p>
        </div>
        <Link className="btn" to="/questions">
          Question bank
        </Link>
      </div>

      <div className="card">
        <div className="card-header">
          <h2>Add a topic</h2>
        </div>
        <div className="card-body">
          <div className="grid-2">
            <div className="field">
              <label className="required" htmlFor="topic-name">
                Name
              </label>
              <input
                id="topic-name"
                type="text"
                value={name}
                placeholder="e.g. Networking"
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') create();
                }}
              />
            </div>
            <div className="field">
              <label htmlFor="topic-description">Description</label>
              <input
                id="topic-description"
                type="text"
                value={description}
                placeholder="Optional"
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>
          <button
            type="button"
            className="btn btn-primary"
            style={{ marginTop: 16 }}
            onClick={create}
            disabled={!name.trim() || creating}
          >
            {creating ? 'Creating…' : 'Create topic'}
          </button>
          <p className="field-hint">
            Topics are also created automatically when a question or CSV row references a new name.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <input
            type="search"
            value={search}
            placeholder="Search topics"
            onChange={(event) => setSearch(event.target.value)}
            style={{ maxWidth: 280 }}
          />
          <span className="subtle">{topics.length} topic(s)</span>
        </div>

        {error ? (
          <div className="card-body">
            <ErrorSummary error={error} />
          </div>
        ) : loading ? (
          <div className="empty">
            <Spinner label="Loading topics…" />
          </div>
        ) : topics.length === 0 ? (
          <div className="empty">No topics yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Slug</th>
                  <th className="cell-main">Description</th>
                  <th>Questions</th>
                  <th>Active</th>
                  <th>Created</th>
                  <th className="cell-actions">Actions</th>
                </tr>
              </thead>
              <tbody>
                {topics.map((topic) => (
                  <tr key={topic.id}>
                    <td className="cell-title">{topic.name}</td>
                    <td className="mono subtle">{topic.slug}</td>
                    <td className="cell-main subtle">{topic.description ?? '—'}</td>
                    <td>
                      {topic.questionCount ? (
                        <Link to={`/questions?topicId=${topic.id}`}>{topic.questionCount}</Link>
                      ) : (
                        <span className="subtle">0</span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${topic.isActive ? 'badge-active' : 'badge-neutral'}`}>
                        {topic.isActive ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                    </td>
                    <td className="subtle">{formatDate(topic.createdAt)}</td>
                    <td className="cell-actions">
                      <div className="row tight" style={{ justifyContent: 'flex-end' }}>
                        <button
                          type="button"
                          className="btn btn-sm"
                          onClick={() => {
                            setEditing(topic);
                            setEditName(topic.name);
                            setEditDescription(topic.description ?? '');
                          }}
                        >
                          Rename
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => toggleActive(topic)}>
                          {topic.isActive ? 'Deactivate' : 'Activate'}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          style={{ color: 'var(--c-danger)' }}
                          onClick={() => setDeleting(topic)}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <Modal
          title={`Rename "${editing.name}"`}
          onClose={() => setEditing(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setEditing(null)} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" onClick={saveEdit} disabled={busy}>
                {busy ? 'Saving…' : 'Save'}
              </button>
            </>
          }
        >
          <div className="field">
            <label className="required" htmlFor="edit-topic-name">
              Name
            </label>
            <input
              id="edit-topic-name"
              type="text"
              value={editName}
              onChange={(event) => setEditName(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="edit-topic-description">Description</label>
            <input
              id="edit-topic-description"
              type="text"
              value={editDescription}
              onChange={(event) => setEditDescription(event.target.value)}
            />
          </div>
          {editing.questionCount ? (
            <p className="field-hint">
              This will update the tag shown on {editing.questionCount} question(s).
            </p>
          ) : null}
        </Modal>
      )}

      {deleting && (
        <Modal
          title={`Delete "${deleting.name}"?`}
          onClose={() => setDeleting(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setDeleting(null)} disabled={busy}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => confirmDelete(Boolean(deleting.questionCount))}
                disabled={busy}
              >
                {busy
                  ? 'Deleting…'
                  : deleting.questionCount
                    ? `Delete and untag ${deleting.questionCount} question(s)`
                    : 'Delete topic'}
              </button>
            </>
          }
        >
          {deleting.questionCount ? (
            <div className="alert alert-warning">
              <strong>
                This topic is assigned to {deleting.questionCount} question(s).
              </strong>
              Deleting it removes the tag from those questions. Historical attempt reports keep the
              topic name they were recorded with, so reporting is unaffected. A question that would
              be left with no topics at all will block the change.
            </div>
          ) : (
            <p style={{ margin: 0 }}>This topic is not assigned to any question.</p>
          )}
        </Modal>
      )}
    </div>
  );
}
