import NewPlaybook from "@/components/NewPlaybook";

// A static segment, so it wins over /playbooks/[intent] for "new".
export default function Page() {
  return <NewPlaybook />;
}
