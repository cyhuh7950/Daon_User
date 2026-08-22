import { NotebookProductWorkspace } from "../../../components/notebook-product-workspace.jsx";

export default async function NotebookPage({ params }) {
  const { notebook_id: notebookId } = await params;
  return <NotebookProductWorkspace notebookId={notebookId} />;
}
