import { useState } from "react";
import AggregatedSummaryView from "./views/AggregatedSummaryView";
import DocumentsView from "./views/DocumentsView";
import ProjectView from "./views/ProjectView";
import ProjectsView from "./views/ProjectsView";
import WorkspaceView from "./views/WorkspaceView";

type View =
  | { kind: "documents" }
  | { kind: "workspace"; docId: number; autoRun: boolean }
  | { kind: "summary" }
  | { kind: "projects" }
  | { kind: "project"; projectId: number };

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

  if (view.kind === "projects") {
    return (
      <ProjectsView
        onOpen={(projectId) => setView({ kind: "project", projectId })}
        onBack={() => setView({ kind: "documents" })}
      />
    );
  }

  if (view.kind === "project") {
    return (
      <ProjectView
        projectId={view.projectId}
        onBack={() => setView({ kind: "projects" })}
      />
    );
  }

  return (
    <DocumentsView
      onOpen={(docId, autoRun = false) => setView({ kind: "workspace", docId, autoRun })}
      onSummary={() => setView({ kind: "summary" })}
      onProjects={() => setView({ kind: "projects" })}
    />
  );
}
