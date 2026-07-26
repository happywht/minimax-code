/**
 * Markdown renderer configuration for assistant / system messages.
 *
 * Wires react-markdown with GFM + math (KaTeX) and the custom code
 * renderer (shiki blocks, mermaid diagrams, inline pills). Links
 * always open in a new tab.
 */
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";
import { MarkdownCode } from "./CodeBlock";

export function MarkdownBody({ text }: { text: string }): JSX.Element {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
      components={{
        code: MarkdownCode as never,
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noreferrer noopener"
            className="text-accent underline-offset-2 hover:underline"
          >
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
