import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Button, Input } from "../../ui";

/** Searches only the already-loaded, escaped preview; it does not read the original file. */
export function SearchableTextPreview({ text, label, className }: { text: string; label: string; className: string }): JSX.Element {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const active = useRef<HTMLElement | null>(null);
  const matches = useMemo(() => {
    if (!query) return [];
    const positions: number[] = [];
    let from = 0;
    while (positions.length < 1000) {
      const position = text.indexOf(query, from);
      if (position < 0) break;
      positions.push(position);
      from = position + query.length;
    }
    return positions;
  }, [text, query]);
  const current = Math.min(index, Math.max(0, matches.length - 1));
  useEffect(() => { if (matches.length) active.current?.scrollIntoView({ block: "nearest" }); }, [current, matches]);
  const pieces: ReactNode[] = [];
  let end = 0;
  matches.forEach((position, i) => {
    pieces.push(text.slice(end, position));
    pieces.push(<mark key={position} ref={i === current ? active : undefined} className={i === current ? "bg-yellow-400 text-black" : "bg-yellow-200 text-black"}>{text.slice(position, position + query.length)}</mark>);
    end = position + query.length;
  });
  pieces.push(text.slice(end));
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <Input aria-label={`查找${label}`} value={query} maxLength={200} placeholder="在当前预览中查找（区分大小写）" onChange={(event) => { setQuery(event.target.value); setIndex(0); }} />
      <Button type="button" size="sm" variant="secondary" disabled={!matches.length} onClick={() => setIndex((current + matches.length - 1) % matches.length)}>上一个</Button>
      <Button type="button" size="sm" variant="secondary" disabled={!matches.length} onClick={() => setIndex((current + 1) % matches.length)}>下一个</Button>
      {query && <span role="status" className="text-xs">{matches.length ? `${current + 1} / ${matches.length}${matches.length === 1000 ? "（最多显示前 1000 处）" : ""}` : "当前预览中无匹配"}</span>}
    </div>
    <p className="text-xs text-ink-2">仅查找当前已加载的文本预览；未显示的内容不会被搜索。</p>
    <pre className={className} aria-label={label}>{pieces}</pre>
  </div>;
}
