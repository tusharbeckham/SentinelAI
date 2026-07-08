import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import App from '@/App'
import '@/index.css'

/**
 * A blank black page is the worst possible failure mode: it hides the actual
 * error behind a colour. This boundary prints the message and stack on screen,
 * so a bad render is diagnosable without opening devtools.
 */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
	state = { error: null as Error | null }

	static getDerivedStateFromError(error: Error) {
		return { error }
	}

	componentDidCatch(error: Error, info: ErrorInfo) {
		console.error('console render failed:', error, info.componentStack)
	}

	render() {
		const { error } = this.state
		if (!error) return this.props.children
		return (
			<main
				style={{
					fontFamily: 'ui-monospace, Menlo, monospace',
					color: '#f2f4f7',
					background: '#0a0b0d',
					minHeight: '100vh',
					padding: '32px',
				}}
			>
				<h1 style={{ color: '#e97366', fontSize: '20px' }}>Console failed to render</h1>
				<p style={{ color: 'rgba(242,244,247,0.62)' }}>{error.message}</p>
				<pre style={{ whiteSpace: 'pre-wrap', fontSize: '13px', opacity: 0.7 }}>
					{error.stack}
				</pre>
			</main>
		)
	}
}

const host = document.getElementById('root')
if (!host) throw new Error('#root missing from index.html')

createRoot(host).render(
	<StrictMode>
		<ErrorBoundary>
			<App />
		</ErrorBoundary>
	</StrictMode>,
)
