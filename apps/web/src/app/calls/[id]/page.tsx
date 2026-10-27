import { CallView } from "./call-view";

export default async function CallPage({ params }: { params: Promise<{ id: string }> }) {
  return <CallView id={(await params).id} wsBase={process.env.NEXT_PUBLIC_API_WS_URL ?? "ws://127.0.0.1:8000"} />;
}
