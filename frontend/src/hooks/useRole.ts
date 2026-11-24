import { ROLE_RANK, type Role } from "../types";
import { useAppSelector } from "../store";

export function useRole() {
  const role = useAppSelector((s) => s.auth.user?.role ?? "viewer") as Role;
  return { role, has: (minimum: Role) => ROLE_RANK[role] >= ROLE_RANK[minimum] };
}
