import MemoryBrowser from '../components/MemoryBrowser'

export default function MemoryPage({ embedded = false }) {
  return <div className="h-full overflow-y-auto"><MemoryBrowser embedded={embedded} /></div>
}
