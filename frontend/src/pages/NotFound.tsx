import { EmptyState, LinkButton } from "../components/ui";

export default function NotFound() {
  return (
    <EmptyState title="Page not found" action={<LinkButton to="/" variant="primary">Go to dashboard</LinkButton>}>
      The address does not match any page in this app.
    </EmptyState>
  );
}
