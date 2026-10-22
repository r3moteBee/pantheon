import MemoryBrowser from '../components/MemoryBrowser'
import usePageTitle from '../hooks/usePageTitle'
import { useStore } from '../store'

export default function MemoryPage({ embedded = false }) {
  // Embedded in the project tabs, ChatTabs owns the title.
  const projectName = useStore((s) => s.activeProject?.name)
  usePageTitle(!embedded && (projectName || 'Project'), 'Memory')
  return <div className="h-full overflow-y-auto"><MemoryBrowser embedded={embedded} /></div>
}
