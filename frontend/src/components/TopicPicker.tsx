/** Topic tagging control: pick from existing topics or type a new one. */

import { useEffect, useMemo, useState, type ReactNode } from 'react';

import { api } from '../api/client';
import type { Topic } from '../api/types';

export function TopicPicker({
  selected,
  onChange,
  disabled = false,
}: {
  /** Topic names. Names are used rather than ids so a brand-new topic needs no round trip. */
  selected: string[];
  onChange: (topics: string[]) => void;
  disabled?: boolean;
}): ReactNode {
  const [available, setAvailable] = useState<Topic[]>([]);
  const [draft, setDraft] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .listTopics()
      .then((topics) => {
        if (!cancelled) setAvailable(topics);
      })
      .catch(() => {
        // A failed topic list must not block authoring — the admin can still type a name.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedKeys = useMemo(
    () => new Set(selected.map((name) => name.trim().toLowerCase())),
    [selected],
  );

  const suggestions = useMemo(
    () => available.filter((topic) => !selectedKeys.has(topic.name.toLowerCase())),
    [available, selectedKeys],
  );

  function add(name: string): void {
    const clean = name.trim();
    if (!clean || selectedKeys.has(clean.toLowerCase())) {
      setDraft('');
      return;
    }
    onChange([...selected, clean]);
    setDraft('');
  }

  function remove(name: string): void {
    onChange(selected.filter((topic) => topic !== name));
  }

  return (
    <div>
      <div className="row tight" style={{ marginBottom: selected.length ? 10 : 0 }}>
        {selected.map((name) => (
          <span className="tag" key={name}>
            {name}
            {!disabled && (
              <button type="button" onClick={() => remove(name)} aria-label={`Remove ${name}`}>
                ×
              </button>
            )}
          </span>
        ))}
      </div>

      {!disabled && (
        <div className="row tight">
          <input
            type="text"
            list="topic-suggestions"
            value={draft}
            placeholder="Add a topic and press Enter"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add(draft);
              }
            }}
            style={{ maxWidth: 280 }}
          />
          <datalist id="topic-suggestions">
            {suggestions.map((topic) => (
              <option key={topic.id} value={topic.name} />
            ))}
          </datalist>
          <button type="button" className="btn btn-sm" onClick={() => add(draft)} disabled={!draft.trim()}>
            Add
          </button>
        </div>
      )}

      {!disabled && suggestions.length > 0 && (
        <div className="field-hint">
          Existing:{' '}
          {suggestions.slice(0, 8).map((topic, index) => (
            <span key={topic.id}>
              {index > 0 && ', '}
              <button
                type="button"
                className="btn-ghost"
                style={{ border: 0, background: 'none', padding: 0, cursor: 'pointer', color: 'inherit', textDecoration: 'underline' }}
                onClick={() => add(topic.name)}
              >
                {topic.name}
              </button>
            </span>
          ))}
          {suggestions.length > 8 ? ' …' : ''}
        </div>
      )}
    </div>
  );
}
