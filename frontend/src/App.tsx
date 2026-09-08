import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  Alert,
  App as AntApp,
  Button,
  Layout,
  Menu,
  Spin,
  Typography,
} from "antd";
import { api, token } from "./api";
import { PermissionProvider } from "./permissions";
import {
  AuditPage,
  ChatPage,
  ConversationsPage,
  DashboardPage,
  DocumentsPage,
  FAQPage,
  GapsPage,
  KnowledgePage,
  LoginPage,
  ModelConfigPage,
  ResourcePage,
} from "./pages";

const { Header, Sider, Content } = Layout;
const menu = [
  ["dashboard", "运营看板", "dashboard:view"],
  ["chat", "AI 问答", "chat:use"],
  ["knowledge", "知识库管理", "knowledge:read"],
  ["documents", "知识单元与 ACL", "knowledge:read"],
  ["users", "用户管理", "user:manage"],
  ["departments", "部门树", "department:manage"],
  ["roles", "角色管理", "role:manage"],
  ["permissions", "功能权限", "role:manage"],
  ["members", "知识库成员", "knowledge:update"],
  ["conversations", "历史会话", "chat:use"],
  ["faq-candidates", "FAQ 候选审核", "faq:manage"],
  ["faqs", "已发布 FAQ", "knowledge:read"],
  ["gaps", "知识缺口", "gap:manage"],
  ["audits", "问答审计", "audit:view"],
  ["system", "模型服务配置", "user:manage"],
];

function Gate({
  code,
  permissions,
  children,
}: {
  code: string;
  permissions: Set<string>;
  children: ReactElement;
}) {
  return permissions.has(code) ? (
    children
  ) : (
    <Alert type="error" showIcon message="无权访问此功能" />
  );
}

function Shell() {
  const nav = useNavigate(),
    location = useLocation();
  const [me, setMe] = useState<any>();
  useEffect(() => {
    api("/auth/me")
      .then(setMe)
      .catch(() => nav("/login"));
  }, []);
  useEffect(() => {
    if (!me || location.pathname === "/login") return;
    const selectedKb = sessionStorage.getItem("kb_id");
    api("/metrics/page-view", {
      method: "POST",
      body: JSON.stringify({
        route: location.pathname,
        kb_id: selectedKb || null,
      }),
    }).catch(() => undefined);
  }, [me?.id, location.pathname]);
  if (!me)
    return (
      <div className="loading">
        <Spin description="正在校验登录态" />
      </div>
    );
  const permissions = new Set<string>(me.permissions || []),
    selected = location.pathname.split("/")[1] || "dashboard";
  const gate = (code: string, node: ReactElement) => (
    <Gate code={code} permissions={permissions}>
      {node}
    </Gate>
  );
  const items = menu
    .filter((x) => permissions.has(x[2]))
    .map(([key, label]) => ({ key, label }));
  return (
    <PermissionProvider permissions={me.permissions || []}>
      <Layout className="app-shell">
        <Sider width={230} theme="light">
          <div className="brand">2.9 企业知识库</div>
          <Menu
            mode="inline"
            selectedKeys={[selected]}
            items={items}
            onClick={(x) => nav("/" + x.key)}
          />
        </Sider>
        <Layout>
          <Header className="top">
            <Typography.Text>{me.username}</Typography.Text>
            <Button
              onClick={() => {
                sessionStorage.clear();
                window.location.assign("/login");
              }}
            >
              退出
            </Button>
          </Header>
          <Content className="content">
            <Routes>
              <Route
                path="/dashboard"
                element={gate("dashboard:view", <DashboardPage />)}
              />
              <Route path="/chat" element={gate("chat:use", <ChatPage />)} />
              <Route
                path="/knowledge"
                element={gate("knowledge:read", <KnowledgePage />)}
              />
              <Route
                path="/documents"
                element={gate("knowledge:read", <DocumentsPage />)}
              />
              <Route
                path="/faq-candidates"
                element={gate("faq:manage", <FAQPage candidates />)}
              />
              <Route
                path="/faqs"
                element={gate("knowledge:read", <FAQPage />)}
              />
              <Route path="/gaps" element={gate("gap:manage", <GapsPage />)} />
              <Route
                path="/audits"
                element={gate("audit:view", <AuditPage />)}
              />
              <Route
                path="/system"
                element={gate("user:manage", <ModelConfigPage />)}
              />
              <Route
                path="/users"
                element={gate(
                  "user:manage",
                  <ResourcePage title="用户管理" path="/users" />,
                )}
              />
              <Route
                path="/departments"
                element={gate(
                  "department:manage",
                  <ResourcePage title="部门树" path="/departments" />,
                )}
              />
              <Route
                path="/roles"
                element={gate(
                  "role:manage",
                  <ResourcePage title="角色管理" path="/roles" />,
                )}
              />
              <Route
                path="/permissions"
                element={gate(
                  "role:manage",
                  <ResourcePage title="功能权限管理" path="/permissions" />,
                )}
              />
              <Route
                path="/members"
                element={gate(
                  "knowledge:update",
                  <ResourcePage
                    title="知识库成员管理"
                    path={`/knowledge-bases/${sessionStorage.getItem("kb_id") || "not-selected"}/members`}
                  />,
                )}
              />
              <Route
                path="/conversations"
                element={gate("chat:use", <ConversationsPage />)}
              />
              <Route
                path="*"
                element={
                  <Navigate to={items.length ? "/" + items[0].key : "/login"} />
                }
              />
            </Routes>
          </Content>
        </Layout>
      </Layout>
    </PermissionProvider>
  );
}

export default function App() {
  return (
    <AntApp>
      {token() ? (
        <Shell />
      ) : (
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="*" element={<Navigate to="/login" />} />
        </Routes>
      )}
    </AntApp>
  );
}
