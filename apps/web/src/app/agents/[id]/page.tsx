import { AgentEditor } from "@/components/agent-editor";

export default async function AgentPage({ params }: { params: Promise<{ id: string }> }) {
  return <AgentEditor id={(await params).id} />;
}
