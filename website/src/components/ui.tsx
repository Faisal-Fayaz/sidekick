import { useState } from 'react';

export function CodeBlock({ code, lang = 'bash' }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <div className="codeblock" data-lang={lang}>
      <button className="copy-btn" onClick={copy}>
        {copied ? '✓ copied' : '⧉ copy'}
      </button>
      <pre>
        <code>{renderCode(code)}</code>
      </pre>
    </div>
  );
}

function renderCode(code: string) {
  return code.split('\n').map((line, i) => {
    const trimmed = line.trimStart();
    if (trimmed.startsWith('#')) {
      return (
        <span key={i}>
          <span className="c-comment">{line}</span>
          {'\n'}
        </span>
      );
    }
    if (trimmed.startsWith('$') || trimmed.startsWith('➜')) {
      return (
        <span key={i}>
          <span className="c-prompt">{line.slice(0, line.indexOf(trimmed[0]) + 1)}</span>
          <span className="c-cmd">{line.slice(line.indexOf(trimmed[0]) + 1)}</span>
          {'\n'}
        </span>
      );
    }
    return (
      <span key={i}>
        <span className="c-cmd">{line}</span>
        {'\n'}
      </span>
    );
  });
}

export function SectionHeading({ prompt, title, sub }: { prompt: string; title: string; sub?: string }) {
  return (
    <div>
      <div className="sec-head">
        <span className="prompt">{prompt}</span>
        <h2>{title}</h2>
      </div>
      {sub && <p className="sec-sub">{sub}</p>}
    </div>
  );
}
