import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** 챗봇 답변을 마크다운으로 렌더링합니다. 링크는 새 탭에서 엽니다. */
export default function ChatMarkdown({ children, className = '' }) {
  return (
    <div className={`md-body ${className}`}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer noopener" />,
        }}
      >
        {children || ''}
      </Markdown>
    </div>
  )
}
