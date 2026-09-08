import { createContext, ReactNode, useContext } from "react";

const PermissionContext = createContext<Set<string>>(new Set());

export function PermissionProvider({
  permissions,
  children,
}: {
  permissions: string[];
  children: ReactNode;
}) {
  return (
    <PermissionContext.Provider value={new Set(permissions)}>
      {children}
    </PermissionContext.Provider>
  );
}

export function usePermission(code: string) {
  return useContext(PermissionContext).has(code);
}
