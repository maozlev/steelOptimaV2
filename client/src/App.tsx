import { useState } from "react";
import ThemeToggle from "./components/ThemeToggle";
import AggregatedSummaryView from "./views/AggregatedSummaryView";
import DocTablesView from "./views/DocTablesView";
import MergedSummaryView from "./views/MergedSummaryView";
import ProjectView from "./views/ProjectView";
import ProjectsView from "./views/ProjectsView";
import TableReviewView from "./views/TableReviewView";
import WorkspaceView from "./views/WorkspaceView";

// The hierarchy is strict: projects hold documents, documents hold tables and
// holes. The app opens on projects; every back-arrow walks one level up.
type View =
  | { kind: "projects" }
  | { kind: "project"; projectId: number }
  | { kind: "docTables"; docId: number; projectId: number }
  | { kind: "workspace"; docId: number; projectId: number; autoRun: boolean }
  | { kind: "tableReview"; tableId: number; projectId: number; docId?: number }
  | { kind: "summary" }
  | { kind: "mergedSummary" };

function RoutedView() {
  const [view, setView] = useState<View>({ kind: "projects" });

  if (view.kind === "workspace") {
    return (
      <WorkspaceView
        docId={view.docId}
        autoRun={view.autoRun}
        onBack={() => setView({ kind: "project", projectId: view.projectId })}
      />
    );
  }

  if (view.kind === "docTables") {
    return (
      <DocTablesView
        docId={view.docId}
        onBack={() => setView({ kind: "project", projectId: view.projectId })}
        onOpenTable={(tableId) =>
          setView({
            kind: "tableReview",
            tableId,
            projectId: view.projectId,
            docId: view.docId,
          })
        }
        onOpenDrawing={() =>
          setView({
            kind: "workspace",
            docId: view.docId,
            projectId: view.projectId,
            autoRun: false,
          })
        }
      />
    );
  }

  if (view.kind === "summary") {
    return <AggregatedSummaryView onBack={() => setView({ kind: "projects" })} />;
  }

  if (view.kind === "project") {
    return (
      <ProjectView
        projectId={view.projectId}
        onBack={() => setView({ kind: "projects" })}
        onOpenTable={(tableId) =>
          setView({ kind: "tableReview", tableId, projectId: view.projectId })
        }
        onOpenDocTables={(docId) =>
          setView({ kind: "docTables", docId, projectId: view.projectId })
        }
        onOpenDrawing={(docId) =>
          setView({
            kind: "workspace",
            docId,
            projectId: view.projectId,
            autoRun: false,
          })
        }
      />
    );
  }

  if (view.kind === "tableReview") {
    return (
      <TableReviewView
        tableId={view.tableId}
        onBack={() =>
          setView(
            view.docId != null
              ? { kind: "docTables", docId: view.docId, projectId: view.projectId }
              : { kind: "project", projectId: view.projectId },
          )
        }
      />
    );
  }

  if (view.kind === "mergedSummary") {
    return <MergedSummaryView onBack={() => setView({ kind: "projects" })} />;
  }

  return (
    <ProjectsView
      onOpen={(projectId) => setView({ kind: "project", projectId })}
      onMergedSummary={() => setView({ kind: "mergedSummary" })}
      onCutoutBom={() => setView({ kind: "summary" })}
    />
  );
}

export default function App() {
  return (
    <>
      <RoutedView />
      <ThemeToggle />
    </>
  );
}
