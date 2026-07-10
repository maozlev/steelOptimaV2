import { useState } from "react";
import AggregatedSummaryView from "./views/AggregatedSummaryView";
import DocumentsView from "./views/DocumentsView";
import WorkspaceView from "./views/WorkspaceView";

type View =
  | { kind: "documents" }
  | { kind: "workspace"; docId: number; autoRun: boolean }
  | { kind: "summary" };

export default function App() {
  const [view, setView] = useState<View>({ kind: "documents" });

  if (view.kind === "workspace") {
    return (
      <WorkspaceView
        docId={view.docId}
        autoRun={view.autoRun}
        onBack={() => setView({ kind: "documents" })}
      />
    );
  }

  if (view.kind === "summary") {
    return <AggregatedSummaryView onBack={() => setView({ kind: "documents" })} />;
  }

  return (
    <DocumentsView
      onOpen={(docId, autoRun = false) => setView({ kind: "workspace", docId, autoRun })}
      onSummary={() => setView({ kind: "summary" })}
    />
  );
}
