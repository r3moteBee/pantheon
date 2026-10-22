import Projects from '../components/Projects'
import usePageTitle from '../hooks/usePageTitle'

export default function ProjectsPage() {
  usePageTitle('Projects')
  return <div className="h-full overflow-y-auto"><Projects /></div>
}
