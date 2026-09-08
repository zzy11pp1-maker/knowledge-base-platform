import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popover,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Tree,
  TreeSelect,
  Typography,
  Upload,
  message,
} from "antd";
import ReactMarkdown from "react-markdown";
import * as echarts from "echarts";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark.css";
import { api, streamChat } from "./api";
import { usePermission } from "./permissions";

const kbId = () => sessionStorage.getItem("kb_id") || "";
const errorText = (e: unknown) => (e instanceof Error ? e.message : "操作失败");
const flattenTree = (rows: any[]): any[] =>
  rows.flatMap((x) => [x, ...flattenTree(x.children || [])]);
const departmentTree = (rows: any[]) => {
  const nodes = new Map(
    rows.map((x) => [
      x.id,
      { title: x.name, value: x.id, children: [] as any[] },
    ]),
  );
  const roots: any[] = [];
  rows.forEach((x) =>
    x.parent_id && nodes.has(x.parent_id)
      ? nodes.get(x.parent_id)!.children.push(nodes.get(x.id)!)
      : roots.push(nodes.get(x.id)!),
  );
  return roots;
};
const permissionTree = (rows: any[] = []) =>
  Object.entries(
    rows.reduce((groups: Record<string, any[]>, x: any) => {
      (groups[x.code.split(":")[0]] ||= []).push(x);
      return groups;
    }, {}),
  ).map(([group, values]) => ({
    key: `group:${group}`,
    title: group,
    checkable: false,
    children: (values || []).map((x: any) => ({
      key: x.code,
      title: `${x.description} (${x.code})`,
    })),
  }));

function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      components={{
        code({ children, className }) {
          const text = String(children).replace(/\n$/, "");
          const html = hljs.highlightAuto(text).value;
          return (
            <span className={className}>
              <Button
                size="small"
                className="copy-code"
                onClick={() => navigator.clipboard.writeText(text)}
              >
                复制代码
              </Button>
              <code dangerouslySetInnerHTML={{ __html: html }} />
            </span>
          );
        },
      }}
    >
      {children}
    </ReactMarkdown>
  );
}

export function LoginPage() {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <Card title="2.9 企业知识库管理平台" className="login-card">
        <Form
          layout="vertical"
          onFinish={async (v) => {
            setBusy(true);
            try {
              const r: any = await api("/auth/login", {
                method: "POST",
                body: JSON.stringify(v),
              });
              sessionStorage.setItem("access_token", r.access_token);
              nav("/dashboard");
              location.reload();
            } catch (e) {
              message.error(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Form.Item name="tenant_id" label="租户" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Button htmlType="submit" type="primary" block loading={busy}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}

export function ResourcePage({ title, path }: { title: string; path: string }) {
  const [rows, setRows] = useState<any[]>([]),
    [options, setOptions] = useState<any>({
      permissions: [],
      roles: [],
      departments: [],
    });
  const [open, setOpen] = useState(false),
    [editing, setEditing] = useState<any>();
  const [form] = Form.useForm();
  const kind = title.includes("用户")
    ? "user"
    : title.includes("部门")
      ? "department"
      : title.includes("角色")
        ? "role"
        : title.includes("成员")
          ? "member"
          : "";
  const load = () =>
    api<any[]>(path)
      .then(setRows)
      .catch((e) => message.error(errorText(e)));
  useEffect(() => {
    load();
    Promise.allSettled([
      api<any[]>("/permissions"),
      api<any[]>("/roles"),
      api<any[]>("/departments"),
    ]).then(([p, r, d]) =>
      setOptions({
        permissions: p.status === "fulfilled" ? p.value : [],
        roles: r.status === "fulfilled" ? r.value : [],
        departments: d.status === "fulfilled" ? d.value : [],
      }),
    );
  }, [path]);
  const save = async (v: any) => {
    let target = path,
      method = "POST",
      body = v;
    if (editing) {
      method = "PATCH";
      const id = kind === "member" ? editing.user_id : editing.id;
      target = `${path}/${id}`;
      if (kind === "user") {
        body = {
          department_id: v.department_id ?? null,
          role_ids: v.role_ids || [],
          is_active: v.is_active,
        };
        if (v.password) body.password = v.password;
      } else if (kind === "department") {
        body = { name: v.name, parent_id: v.parent_id ?? null };
      } else if (kind === "role") {
        body = { name: v.name, permission_codes: v.permission_codes || [] };
      } else if (kind === "member") {
        body = { role: v.role };
      }
    }
    await api(target, { method, body: JSON.stringify(body) });
    setOpen(false);
    setEditing(undefined);
    form.resetFields();
    load();
  };
  const remove = (row: any) => {
    const id = kind === "member" ? row.user_id : row.id;
    Modal.confirm({
      title: "确认删除？",
      onOk: async () => {
        await api(`${path}/${id}`, { method: "DELETE" });
        load();
      },
    });
  };
  const columns: any[] = Object.keys(rows[0] || { id: "", name: "" })
    .filter((x) => !["password_hash", "children"].includes(x))
    .map((key) => ({
      title: key,
      dataIndex: key,
      render: (v: any) =>
        typeof v === "object" ? JSON.stringify(v) : String(v ?? ""),
    }));
  if (kind) {
    columns.push({
      title: "操作",
      render: (_: any, row: any) => (
        <Space>
          {!(kind === "role" && row.is_system) && (
            <Button
              onClick={() => {
                setEditing(row);
                form.setFieldsValue({ ...row, password: undefined });
                setOpen(true);
              }}
            >
              编辑
            </Button>
          )}
          {kind !== "user" && !(kind === "role" && row.is_system) && (
            <Button danger onClick={() => remove(row)}>
              删除
            </Button>
          )}
        </Space>
      ),
    });
  }
  return (
    <Card
      title={title}
      extra={
        kind && (
          <Button
            type="primary"
            onClick={() => {
              setEditing(undefined);
              form.resetFields();
              setOpen(true);
            }}
          >
            新增
          </Button>
        )
      }
    >
      <Table
        rowKey={(x) => x.id || x.code || x.user_id}
        dataSource={rows}
        columns={columns}
      />
      <Modal
        title={`${editing ? "编辑" : "新增"}${title}`}
        open={open}
        onCancel={() => {
          setOpen(false);
          setEditing(undefined);
        }}
        onOk={() => form.submit()}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(v) => save(v).catch((e) => message.error(errorText(e)))}
        >
          {kind === "user" && (
            <>
              <Form.Item
                name="username"
                label="用户名"
                rules={[{ required: true }]}
              >
                <Input disabled={!!editing} />
              </Form.Item>
              <Form.Item
                name="password"
                label={
                  editing ? "新密码（留空则不修改）" : "初始密码（至少 12 位）"
                }
                rules={[{ required: !editing, min: 12 }]}
              >
                <Input.Password />
              </Form.Item>
              <Form.Item name="department_id" label="部门">
                <Select
                  allowClear
                  options={flattenTree(options.departments).map((x: any) => ({
                    value: x.id,
                    label: x.name,
                  }))}
                />
              </Form.Item>
              <Form.Item name="role_ids" label="角色">
                <Select
                  mode="multiple"
                  options={options.roles.map((x: any) => ({
                    value: x.id,
                    label: x.name,
                  }))}
                />
              </Form.Item>
              {editing && (
                <Form.Item
                  name="is_active"
                  label="账号启用"
                  valuePropName="checked"
                >
                  <Switch />
                </Form.Item>
              )}
            </>
          )}
          {kind === "department" && (
            <>
              <Form.Item
                name="name"
                label="部门名称"
                rules={[{ required: true }]}
              >
                <Input />
              </Form.Item>
              <Form.Item name="parent_id" label="上级部门">
                <Select
                  allowClear
                  options={flattenTree(options.departments).map((x: any) => ({
                    value: x.id,
                    label: x.name,
                  }))}
                />
              </Form.Item>
            </>
          )}
          {kind === "role" && (
            <>
              <Form.Item
                name="name"
                label="角色名称"
                rules={[{ required: true }]}
              >
                <Input />
              </Form.Item>
              <Form.Item
                name="permission_codes"
                label="菜单/按钮操作权限"
                valuePropName="checkedKeys"
                getValueFromEvent={(keys) => keys}
              >
                <Tree
                  checkable
                  treeData={permissionTree(options.permissions)}
                />
              </Form.Item>
            </>
          )}
          {kind === "member" && (
            <>
              <Form.Item
                name="user_id"
                label="用户 ID"
                rules={[{ required: true }]}
              >
                <Input disabled={!!editing} />
              </Form.Item>
              <Form.Item
                name="role"
                label="成员角色"
                rules={[{ required: true }]}
              >
                <Select
                  options={[
                    { value: "viewer", label: "只读成员" },
                    { value: "editor", label: "知识管理员" },
                  ]}
                />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </Card>
  );
}

export function KnowledgePage() {
  const [rows, setRows] = useState<any[]>([]);
  const canCreate = usePermission("knowledge:create"),
    canDelete = usePermission("knowledge:delete");
  const load = () => api<any[]>("/knowledge-bases").then(setRows);
  useEffect(() => {
    load();
  }, []);
  return (
    <Card
      title="知识库管理"
      extra={
        canCreate && (
          <Button
            type="primary"
            onClick={() =>
              Modal.confirm({
                title: "创建知识库",
                content: <Input id="new-kb" placeholder="知识库名称" />,
                onOk: async () => {
                  const name = (
                    document.getElementById("new-kb") as HTMLInputElement
                  ).value;
                  await api("/knowledge-bases", {
                    method: "POST",
                    body: JSON.stringify({ name }),
                  });
                  load();
                },
              })
            }
          >
            新建
          </Button>
        )
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "说明", dataIndex: "description" },
          {
            title: "操作",
            render: (_: any, r: any) => (
              <Space>
                <Button
                  onClick={() => {
                    sessionStorage.setItem("kb_id", r.id);
                    message.success("已选为当前知识库");
                  }}
                >
                  选择
                </Button>
                {canDelete && (
                  <Button
                    danger
                    onClick={() =>
                      Modal.confirm({
                        title: "确认删除知识库？",
                        onOk: async () => {
                          await api(`/knowledge-bases/${r.id}`, {
                            method: "DELETE",
                          });
                          if (kbId() === r.id)
                            sessionStorage.removeItem("kb_id");
                          load();
                        },
                      })
                    }
                  >
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}

export function DocumentsPage() {
  const current = kbId(),
    canCreate = usePermission("knowledge:create"),
    canUpdate = usePermission("knowledge:update"),
    canDelete = usePermission("knowledge:delete");
  const [rows, setRows] = useState<any[]>([]),
    [uploadOpen, setUploadOpen] = useState(false),
    [files, setFiles] = useState<any[]>([]),
    [progress, setProgress] = useState(0),
    [progressStage, setProgressStage] = useState(""),
    [mode, setMode] = useState<"single" | "batch">("single"),
    [aclDoc, setAclDoc] = useState<any>(),
    [acl, setAcl] = useState<any>({
      global: false,
      department: [],
      role: [],
      user: [],
    }),
    [opts, setOpts] = useState<any>({ departments: [], roles: [], users: [] }),
    [edit, setEdit] = useState<any>();
  const [form] = Form.useForm();
  const load = () =>
    current &&
    api<any[]>(`/documents?kb_id=${encodeURIComponent(current)}`).then(setRows);
  useEffect(() => {
    load();
  }, [current]);
  if (!current) return <Alert message="请先在知识库管理中选择当前知识库" />;
  const waitForJobs = async (ids: string[]) => {
    for (let attempt = 0; attempt < 600; attempt += 1) {
      const jobs = await Promise.all(
        ids.map((id) => api<any>(`/ingestion-jobs/${id}`)),
      );
      setProgress(
        Math.round(
          jobs.reduce((total, job) => total + job.progress, 0) / jobs.length,
        ),
      );
      setProgressStage(jobs.map((job) => job.stage).join("；"));
      const failed = jobs.filter((job) => job.status === "failed");
      if (failed.length)
        throw new Error(
          failed
            .map((job) => `${job.filename}：${job.error_message || "导入失败"}`)
            .join("；"),
        );
      if (jobs.every((job) => job.status === "ready")) return jobs;
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }
    throw new Error("导入任务等待超时，可在知识单元台账中稍后查看状态");
  };
  const submit = async (v: any) => {
    if (!files.length) return message.warning("请选择文件");
    if (mode === "single" && files.length !== 1)
      return message.warning("单篇导入只能选择一个文件");
    const fd = new FormData();
    fd.append("kb_id", current);
    fd.append("category", v.category || "未分类");
    fd.append("chunk_size", String(v.chunk_size || 1000));
    fd.append("chunk_overlap", String(v.chunk_overlap ?? 100));
    files.forEach((x) => {
      const file = x.originFileObj;
      fd.append(
        mode === "single" ? "file" : "files",
        file,
        file.webkitRelativePath || file.name,
      );
    });
    setProgress(5);
    setProgressStage("任务已提交");
    try {
      const result: any = await api(
        mode === "single"
          ? "/documents/import/jobs"
          : "/documents/import/jobs/batch",
        { method: "POST", body: fd },
      );
      const jobs = mode === "single" ? [result] : result.items;
      await waitForJobs(jobs.map((job: any) => job.id));
      message.success(
        mode === "single"
          ? "单篇文档解析、切分和索引完成"
          : `批量完成：成功 ${jobs.length}，失败 0`,
      );
      setFiles([]);
      setUploadOpen(false);
      load();
    } catch (e) {
      message.error(errorText(e));
    }
  };
  const openAcl = async (doc: any) => {
    const [o, a] = await Promise.all([
      api<any>(`/knowledge-bases/${current}/acl-options`),
      api<any>(`/documents/${doc.id}/permissions`),
    ]);
    setOpts(o);
    const value: any = { global: false, department: [], role: [], user: [] };
    a.entries.forEach((x: any) =>
      x.permission_type === "global"
        ? (value.global = true)
        : value[x.permission_type].push(x.target_id),
    );
    setAcl(value);
    setAclDoc(doc);
  };
  const saveAcl = async () => {
    const entries: any[] = [];
    if (acl.global) entries.push({ permission_type: "global" });
    (["department", "role", "user"] as const).forEach((type) =>
      acl[type].forEach((id: string) =>
        entries.push({ permission_type: type, target_id: id }),
      ),
    );
    await api(`/documents/${aclDoc.id}/permissions`, {
      method: "PUT",
      body: JSON.stringify({ entries }),
    });
    setAclDoc(undefined);
    load();
    message.success("四维 ACL 已同步");
  };
  const option = (xs: any[]) => xs.map((x) => ({ value: x.id, label: x.name }));
  return (
    <Card
      title="知识单元台账"
      extra={
        canCreate && (
          <Button type="primary" onClick={() => setUploadOpen(true)}>
            单篇 / 批量导入
          </Button>
        )
      }
    >
      <Table
        rowKey="id"
        scroll={{ x: 1400 }}
        dataSource={rows}
        columns={[
          { title: "ID", dataIndex: "id", ellipsis: true },
          { title: "标题", dataIndex: "title" },
          { title: "格式", dataIndex: "file_type" },
          { title: "分类", dataIndex: "category" },
          {
            title: "权限标签",
            dataIndex: "permission_labels",
            render: (xs: string[]) => (
              <Space wrap>
                {xs.map((x) => (
                  <Tag key={x}>{x}</Tag>
                ))}
              </Space>
            ),
          },
          { title: "更新时间", dataIndex: "updated_at" },
          {
            title: "启用",
            dataIndex: "is_enabled",
            render: (x: boolean) => (
              <Tag color={x ? "green" : "default"}>{x ? "启用" : "停用"}</Tag>
            ),
          },
          {
            title: "状态/进度",
            render: (_: any, r: any) => (
              <>
                <Tag>{r.status}</Tag>
                {r.status === "ready" ? `解析与向量索引 100%` : "处理中"}
              </>
            ),
          },
          { title: "Chunk", dataIndex: "chunk_count" },
          {
            title: "操作",
            fixed: "right",
            render: (_: any, r: any) => (
              <Space>
                {canUpdate && (
                  <>
                    <Button
                      onClick={() =>
                        openAcl(r).catch((e) => message.error(errorText(e)))
                      }
                    >
                      权限
                    </Button>
                    <Button
                      onClick={() => {
                        setEdit(r);
                        form.setFieldsValue(r);
                      }}
                    >
                      编辑
                    </Button>
                    <Upload
                      showUploadList={false}
                      beforeUpload={async (file) => {
                        const fd = new FormData();
                        fd.append("file", file);
                        fd.append("chunk_size", String(r.chunk_size));
                        fd.append("chunk_overlap", String(r.chunk_overlap));
                        try {
                          await api(`/documents/${r.id}/reindex`, {
                            method: "POST",
                            body: fd,
                          });
                          load();
                        } catch (e) {
                          message.error(errorText(e));
                        }
                        return false;
                      }}
                    >
                      <Button>重建索引</Button>
                    </Upload>
                  </>
                )}
                {canDelete && (
                  <Button
                    danger
                    onClick={() =>
                      Modal.confirm({
                        title: "删除后将同步清理向量 Chunk",
                        onOk: async () => {
                          await api(`/documents/${r.id}`, { method: "DELETE" });
                          load();
                        },
                      })
                    }
                  >
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Typography.Title level={5}>知识卡片视图</Typography.Title>
      <Row gutter={[12, 12]}>
        {rows.map((row) => (
          <Col span={8} key={row.id}>
            <Card
              size="small"
              title={row.title}
              extra={
                <Tag color={row.is_enabled ? "green" : "default"}>
                  {row.is_enabled ? "启用" : "停用"}
                </Tag>
              }
            >
              <p>
                {row.category} · {row.file_type} · {row.chunk_count} Chunks
              </p>
              <Space wrap>
                {(row.permission_labels || []).map((label: string) => (
                  <Tag key={label}>{label}</Tag>
                ))}
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
      <Drawer
        title="文档导入"
        size={560}
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
      >
        <Tabs
          activeKey={mode}
          onChange={(x) => {
            setMode(x as any);
            setFiles([]);
          }}
          items={[
            { key: "single", label: "单篇导入" },
            { key: "batch", label: "批量/文件夹导入" },
          ]}
        />
        <Form
          layout="vertical"
          initialValues={{
            category: "未分类",
            chunk_size: 1000,
            chunk_overlap: 100,
          }}
          onFinish={submit}
        >
          <Form.Item label="文件">
            <Upload.Dragger
              multiple={mode === "batch"}
              directory={mode === "batch"}
              accept=".pdf,.docx,.md,.markdown,.txt"
              beforeUpload={() => false}
              fileList={files}
              onChange={(x) => setFiles(x.fileList)}
            >
              <p>
                拖拽{mode === "batch" ? "多个文件或整个文件夹" : "单个文件"}
                到这里
              </p>
            </Upload.Dragger>
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Input />
          </Form.Item>
          <Space>
            <Form.Item name="chunk_size" label="Chunk 大小">
              <InputNumber min={100} max={10000} />
            </Form.Item>
            <Form.Item name="chunk_overlap" label="重叠字符">
              <InputNumber min={0} max={9999} />
            </Form.Item>
          </Space>
          {progress > 0 && (
            <>
              <Progress className="upload-progress" percent={progress} />
              <Typography.Text type="secondary">
                {progressStage}
              </Typography.Text>
            </>
          )}
          <Button htmlType="submit" type="primary">
            开始解析、切分并建立向量索引
          </Button>
        </Form>
      </Drawer>
      <Modal
        title={`四维数据权限：${aclDoc?.title || ""}`}
        open={!!aclDoc}
        onCancel={() => setAclDoc(undefined)}
        onOk={() => saveAcl().catch((e) => message.error(errorText(e)))}
      >
        <Alert
          type="info"
          message="global / department / role / user 任意一维命中即可访问（OR）"
        />
        <Form layout="vertical">
          <Form.Item label="全部知识库成员">
            <Switch
              checked={acl.global}
              onChange={(v) => setAcl({ ...acl, global: v })}
            />
          </Form.Item>
          {(["department", "role", "user"] as const).map((type) => (
            <Form.Item
              key={type}
              label={{ department: "部门", role: "角色", user: "用户" }[type]}
            >
              {type === "department" ? (
                <TreeSelect
                  treeCheckable
                  multiple
                  showCheckedStrategy={TreeSelect.SHOW_ALL}
                  value={acl.department}
                  treeData={departmentTree(opts.departments)}
                  onChange={(v) => setAcl({ ...acl, department: v })}
                />
              ) : (
                <Select
                  mode="multiple"
                  value={acl[type]}
                  options={option(opts[type === "role" ? "roles" : "users"])}
                  onChange={(v) => setAcl({ ...acl, [type]: v })}
                />
              )}
            </Form.Item>
          ))}
        </Form>
      </Modal>
      <Drawer
        title="编辑知识单元"
        size={480}
        open={!!edit}
        onClose={() => setEdit(undefined)}
        extra={
          <Button type="primary" onClick={() => form.submit()}>
            保存
          </Button>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={async (v) => {
            await api(`/documents/${edit.id}`, {
              method: "PATCH",
              body: JSON.stringify({
                title: v.title,
                category: v.category,
                is_enabled: v.is_enabled,
              }),
            });
            setEdit(undefined);
            load();
          }}
        >
          <Form.Item name="title" label="标题" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="category" label="分类" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="is_enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Space>
            <Form.Item name="chunk_size" label="Chunk 大小">
              <InputNumber min={100} max={10000} />
            </Form.Item>
            <Form.Item name="chunk_overlap" label="重叠字符">
              <InputNumber min={0} max={9999} />
            </Form.Item>
          </Space>
          <Alert
            type="info"
            message="调整切片参数需重新上传源文件，系统成功写入新向量后才会替换旧索引。"
          />
          <Upload
            showUploadList={false}
            beforeUpload={async (file) => {
              const fd = new FormData();
              fd.append("file", file);
              fd.append("chunk_size", String(form.getFieldValue("chunk_size")));
              fd.append(
                "chunk_overlap",
                String(form.getFieldValue("chunk_overlap")),
              );
              try {
                await api(`/documents/${edit.id}/reindex`, {
                  method: "POST",
                  body: fd,
                });
                message.success("切片参数调整与重建索引完成");
                setEdit(undefined);
                load();
              } catch (e) {
                message.error(errorText(e));
              }
              return false;
            }}
          >
            <Button style={{ marginTop: 12 }}>上传源文件并应用切片调整</Button>
          </Upload>
        </Form>
      </Drawer>
    </Card>
  );
}

export function ChatPage() {
  const [query, setQuery] = useState(""),
    [answer, setAnswer] = useState(""),
    [history, setHistory] = useState<any[]>([]),
    [citations, setCitations] = useState<any[]>([]),
    [detail, setDetail] = useState<any>(),
    [busy, setBusy] = useState(false),
    [restricted, setRestricted] = useState(false),
    [suggestions, setSuggestions] = useState<any[]>([]),
    [conversationRows, setConversationRows] = useState<any[]>([]),
    [historyOpen, setHistoryOpen] = useState(false);
  const conversation = () =>
    sessionStorage.getItem("conversation_id") || undefined;
  const loadHistory = () =>
    conversation()
      ? api<any[]>(`/conversations/${conversation()}/messages`).then(setHistory)
      : Promise.resolve();
  useEffect(() => {
    loadHistory();
    api<any[]>("/conversations").then(setConversationRows);
    if (kbId())
      api<any[]>(`/knowledge-bases/${kbId()}/suggestions`)
        .then(setSuggestions)
        .catch(() => setSuggestions([]));
  }, []);
  const ask = async () => {
    if (!kbId()) return message.warning("请先选择知识库");
    setBusy(true);
    setAnswer("");
    setCitations([]);
    setRestricted(false);
    try {
      await streamChat(
        { kb_id: kbId(), query, conversation_id: conversation() },
        (event, data) => {
          if (event === "start")
            sessionStorage.setItem("conversation_id", data.conversation_id);
          if (event === "token") setAnswer((x) => x + data.content);
          if (event === "done") {
            setRestricted(Boolean(data.restricted_sources_detected));
            setCitations(
              (data.citations || []).map((c: any) => ({
                ...c,
                message_id: data.message_id,
              })),
            );
            loadHistory();
          }
          if (event === "error") message.error(data.message);
        },
      );
    } catch (e) {
      message.error(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Row gutter={16}>
      <Col span={16}>
        <Card
          title="AI 问答工作台"
          extra={
            <Space>
              <Button onClick={() => setHistoryOpen(true)}>
                历史会话侧边栏
              </Button>
              <Button
                onClick={() => {
                  sessionStorage.removeItem("conversation_id");
                  setHistory([]);
                  setAnswer("");
                }}
              >
                新会话
              </Button>
            </Space>
          }
        >
          {history.length > 0 && (
            <List
              className="history"
              dataSource={history}
              renderItem={(m: any) => (
                <List.Item>
                  <Typography.Text strong>
                    {m.role === "user" ? "我" : "AI"}：
                  </Typography.Text>
                  <Markdown>{m.content}</Markdown>
                </List.Item>
              )}
            />
          )}
          <Space wrap>
            {suggestions.map((x) => (
              <Button key={x.id} onClick={() => setQuery(x.question)}>
                {x.question}
              </Button>
            ))}
          </Space>
          <Space.Compact block>
            <Input.TextArea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoSize={{ minRows: 2, maxRows: 5 }}
            />
            <Button type="primary" loading={busy} onClick={ask}>
              发送
            </Button>
          </Space.Compact>
          {restricted && (
            <Alert
              className="permission-note"
              type="warning"
              showIcon
              message="部分参考资料因权限受限无法展示"
            />
          )}
          <div className="answer">
            <Markdown>{answer}</Markdown>
          </div>
        </Card>
      </Col>
      <Col span={8}>
        <Card title="Citation">
          <List
            dataSource={citations}
            renderItem={(c: any) => (
              <List.Item>
                <Popover
                  title={c.title}
                  content={`${c.source} · Chunk ${c.chunk_index}`}
                >
                  <Button
                    className="citation-button"
                    type="link"
                    onClick={async () => {
                      try {
                        setDetail(
                          await api(`/citations/${c.message_id}/${c.chunk_id}`),
                        );
                      } catch (e) {
                        message.error(errorText(e));
                      }
                    }}
                  >
                    {c.label} {c.title}
                    <br />
                    {c.source} · Chunk {c.chunk_index}
                  </Button>
                </Popover>
              </List.Item>
            )}
          />
          {detail && (
            <Descriptions
              column={1}
              size="small"
              items={[
                { key: "source", label: "来源", children: detail.source },
                { key: "chunk", label: "Chunk", children: detail.chunk_index },
                {
                  key: "content",
                  label: "可访问内容",
                  children: detail.content,
                },
              ]}
            />
          )}
        </Card>
      </Col>
      <Drawer
        title="历史会话"
        placement="left"
        size={380}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
      >
        <List
          dataSource={conversationRows}
          renderItem={(row: any) => (
            <List.Item
              actions={[
                <Button
                  key="resume"
                  type="link"
                  onClick={async () => {
                    sessionStorage.setItem("conversation_id", row.id);
                    sessionStorage.setItem("kb_id", row.kb_id);
                    setHistory(await api(`/conversations/${row.id}/messages`));
                    setHistoryOpen(false);
                  }}
                >
                  恢复
                </Button>,
              ]}
            >
              <List.Item.Meta title={row.title} description={row.created_at} />
            </List.Item>
          )}
        />
      </Drawer>
    </Row>
  );
}

export function ConversationsPage() {
  const nav = useNavigate();
  const [rows, setRows] = useState<any[]>([]),
    [selected, setSelected] = useState<any>(),
    [messages, setMessages] = useState<any[]>([]);
  useEffect(() => {
    api<any[]>("/conversations").then(setRows);
  }, []);
  return (
    <Row gutter={16}>
      <Col span={10}>
        <Card title="历史会话">
          <Table
            rowKey="id"
            dataSource={rows}
            columns={[
              { title: "标题", dataIndex: "title" },
              {
                title: "操作",
                render: (_: any, r: any) => (
                  <Button
                    onClick={async () => {
                      setSelected(r);
                      setMessages(await api(`/conversations/${r.id}/messages`));
                    }}
                  >
                    查看
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      </Col>
      <Col span={14}>
        <Card
          title={selected?.title || "选择会话"}
          extra={
            selected && (
              <Button
                onClick={() => {
                  sessionStorage.setItem("conversation_id", selected.id);
                  sessionStorage.setItem("kb_id", selected.kb_id);
                  nav("/chat");
                }}
              >
                恢复会话
              </Button>
            )
          }
        >
          <List
            dataSource={messages}
            renderItem={(m: any) => (
              <List.Item>
                <Markdown>{m.content}</Markdown>
              </List.Item>
            )}
          />
        </Card>
      </Col>
    </Row>
  );
}

export function FAQPage({ candidates = false }: { candidates?: boolean }) {
  const [rows, setRows] = useState<any[]>([]),
    [review, setReview] = useState<any>(),
    [question, setQuestion] = useState(""),
    [answer, setAnswer] = useState("");
  const path = candidates
    ? `/knowledge-bases/${kbId()}/faq/candidates`
    : `/knowledge-bases/${kbId()}/faqs`;
  const load = () => kbId() && api<any[]>(path).then(setRows);
  useEffect(() => {
    load();
  }, [path]);
  const approve = async () => {
    await api(`/faq/candidates/${review.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "approved", question, answer }),
    });
    setReview(undefined);
    load();
  };
  return (
    <Card
      title={candidates ? "FAQ 候选审核" : "已发布 FAQ"}
      extra={
        candidates && (
          <Button
            onClick={async () => {
              await api(`/knowledge-bases/${kbId()}/faq/mine`, {
                method: "POST",
              });
              load();
            }}
          >
            从历史问题挖掘
          </Button>
        )
      }
    >
      <Table
        rowKey="id"
        dataSource={rows}
        columns={[
          {
            title: "问题簇/标准问题",
            dataIndex: candidates ? "standard_question" : "question",
          },
          ...(candidates
            ? [
                {
                  title: "历史问法样本",
                  dataIndex: "question_samples",
                  render: (values: string[]) => values?.join("；"),
                },
              ]
            : []),
          {
            title: "频次/命中",
            dataIndex: candidates ? "occurrence_count" : "hit_count",
          },
          { title: "置信度", dataIndex: "confidence_score" },
          {
            title: "关联知识单元",
            dataIndex: "source_document_ids",
            render: (x: string[]) => x?.join(", "),
          },
          { title: "状态", dataIndex: "status" },
          {
            title: "标准答案",
            dataIndex: candidates ? "proposed_answer" : "answer",
            ellipsis: true,
          },
          {
            title: "操作",
            render: (_: any, r: any) =>
              candidates ? (
                <Space>
                  {r.status === "pending" && (
                    <>
                      <Button
                        onClick={() => {
                          setReview(r);
                          setQuestion(r.standard_question || "");
                          setAnswer(r.proposed_answer || "");
                        }}
                      >
                        编辑并批准
                      </Button>
                      <Button
                        danger
                        onClick={async () => {
                          await api(`/faq/candidates/${r.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({ status: "rejected" }),
                          });
                          load();
                        }}
                      >
                        拒绝
                      </Button>
                    </>
                  )}
                  {r.status === "approved" && (
                    <Button
                      type="primary"
                      onClick={async () => {
                        await api(`/faq/candidates/${r.id}/publish`, {
                          method: "POST",
                        });
                        load();
                      }}
                    >
                      发布并进入缓存
                    </Button>
                  )}
                </Space>
              ) : (
                <Switch
                  checked={r.enabled}
                  onChange={async (v) => {
                    await api(`/faqs/${r.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({ enabled: v }),
                    });
                    load();
                  }}
                />
              ),
          },
        ]}
      />
      <Modal
        title="编辑标准问答并批准"
        open={!!review}
        onCancel={() => setReview(undefined)}
        onOk={() => approve().catch((e) => message.error(errorText(e)))}
      >
        <Typography.Text>标准问题</Typography.Text>
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          style={{ marginBottom: 12 }}
        />
        <Typography.Text>标准答案</Typography.Text>
        <Input.TextArea
          rows={8}
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
        />
      </Modal>
    </Card>
  );
}

export function GapsPage() {
  const [rows, setRows] = useState<any[]>([]);
  const load = () =>
    kbId() && api<any[]>(`/knowledge-bases/${kbId()}/gaps`).then(setRows);
  useEffect(() => {
    load();
  }, []);
  return (
    <Card title="知识缺口">
      <Table
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "标准问题", dataIndex: "normalized_question" },
          { title: "部门", dataIndex: "department_id" },
          { title: "类型", dataIndex: "gap_type" },
          { title: "频次", dataIndex: "occurrence_count" },
          { title: "最高相似度", dataIndex: "highest_similarity_score" },
          { title: "建议分类", dataIndex: "suggested_category" },
          { title: "状态", dataIndex: "status" },
          {
            title: "操作",
            render: (_: any, r: any) => (
              <Space>
                <Button
                  onClick={async () => {
                    await api(`/gaps/${r.id}/knowledge-task`, {
                      method: "POST",
                    });
                    message.success("已创建知识补充任务");
                  }}
                >
                  一键补充知识
                </Button>
                <Button
                  disabled={r.status === "resolved"}
                  onClick={async () => {
                    await api(`/gaps/${r.id}/resolve`, { method: "POST" });
                    load();
                  }}
                >
                  关闭
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
}

function TrendChart({ data }: { data: any[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { data: ["PV", "问题数", "Token", "平均响应(ms)"] },
      xAxis: { type: "category", data: (data || []).map((x) => x.date) },
      yAxis: { type: "value" },
      series: [
        ["PV", "pv"],
        ["问题数", "questions"],
        ["Token", "tokens"],
        ["平均响应(ms)", "average_response_ms"],
      ].map(([name, key]) => ({
        name,
        type: "line",
        data: (data || []).map((x) => x[key] || 0),
      })),
    });
    return () => chart.dispose();
  }, [data]);
  return <div ref={ref} style={{ height: 340 }} />;
}

export function DashboardPage() {
  const [days, setDays] = useState(7),
    [data, setData] = useState<any>({});
  useEffect(() => {
    api(`/dashboard/summary?days=${days}${kbId() ? `&kb_id=${kbId()}` : ""}`)
      .then(setData)
      .catch((e) => message.error(errorText(e)));
  }, [days]);
  const stats = [
    ["PV", data.pv],
    ["UV", data.uv],
    ["独立提问人数", data.question_uv],
    ["知识单元", data.knowledge_unit_count],
    ["问题数", data.question_count],
    ["当日提问", data.daily_question_count],
    ["当周提问", data.weekly_question_count],
    ["FAQ 命中率", data.faq_hit_rate],
    ["知识覆盖率", data.knowledge_coverage_rate],
    ["Token", data.token_total],
    ["平均响应(ms)", data.average_response_ms],
    ["P50(ms)", data.p50_response_ms],
    ["P95(ms)", data.p95_response_ms],
  ];
  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Card
        title="运营看板"
        extra={
          <Select
            value={days}
            options={[
              { value: 7, label: "最近 7 天" },
              { value: 30, label: "最近 30 天" },
            ]}
            onChange={setDays}
          />
        }
      >
        <Row gutter={[16, 16]}>
          {stats.map(([name, value]) => (
            <Col span={6} key={name}>
              <Card>
                <Statistic title={name} value={value || 0} />
              </Card>
            </Col>
          ))}
        </Row>
      </Card>
      <Card title="访问、问答、Token 与响应时延趋势">
        <TrendChart data={data.trend || []} />
      </Card>
      <Tabs
        items={[
          {
            key: "questions",
            label: "高频问题榜",
            children: (
              <Table
                rowKey="id"
                dataSource={data.top_questions}
                columns={[
                  { title: "问题", dataIndex: "question" },
                  { title: "频次", dataIndex: "count" },
                ]}
              />
            ),
          },
          {
            key: "faq",
            label: "高频 FAQ",
            children: (
              <Table
                rowKey="id"
                dataSource={data.top_faq}
                columns={[
                  { title: "问题", dataIndex: "question" },
                  { title: "命中", dataIndex: "hits" },
                ]}
              />
            ),
          },
          {
            key: "docs",
            label: "知识热度榜",
            children: (
              <Table
                rowKey="document_id"
                dataSource={data.popular_documents}
                columns={[
                  { title: "文档", dataIndex: "title" },
                  { title: "引用", dataIndex: "hits" },
                ]}
              />
            ),
          },
          {
            key: "gaps",
            label: "知识缺口",
            children: (
              <Table
                rowKey="id"
                dataSource={data.top_gaps}
                columns={[
                  { title: "问题", dataIndex: "question" },
                  { title: "次数", dataIndex: "count" },
                ]}
              />
            ),
          },
        ]}
      />
    </Space>
  );
}

export function AuditPage() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    if (kbId())
      api<any[]>(`/knowledge-bases/${kbId()}/question-audits`).then(setRows);
  }, []);
  if (!kbId()) return <Alert message="请先选择知识库" />;
  return (
    <Card title="单次问答鉴权审计">
      <Table
        rowKey="id"
        scroll={{ x: 1600 }}
        dataSource={rows}
        columns={[
          { title: "会话 ID", dataIndex: "conversation_id" },
          { title: "用户 ID", dataIndex: "user_id" },
          { title: "时间", dataIndex: "created_at" },
          { title: "问题", dataIndex: "question_text" },
          {
            title: "召回单元",
            dataIndex: "recalled_document_ids",
            render: (x: any) => x.join(","),
          },
          {
            title: "放行单元",
            dataIndex: "allowed_document_ids",
            render: (x: any) => x.join(","),
          },
          {
            title: "拦截单元",
            dataIndex: "denied_document_ids",
            render: (x: any) => x.join(","),
          },
          {
            title: "Token",
            render: (_: any, r: any) => r.prompt_tokens + r.completion_tokens,
          },
          { title: "耗时(ms)", dataIndex: "response_ms" },
        ]}
      />
    </Card>
  );
}

export function ModelConfigPage() {
  const [form] = Form.useForm();
  useEffect(() => {
    api("/system/model-config").then(form.setFieldsValue);
  }, []);
  return (
    <Card title="底层模型接口配置">
      <Alert
        type="info"
        showIcon
        message="此页面只维护非敏感地址和模型名；API Key 始终由服务器环境变量提供且不会回显。"
      />
      <Form
        form={form}
        layout="vertical"
        style={{ maxWidth: 760, marginTop: 20 }}
        onFinish={async (v) => {
          await api("/system/model-config", {
            method: "PATCH",
            body: JSON.stringify(v),
          });
          message.success("配置已保存，客户端连接池已刷新");
        }}
      >
        <Form.Item name="bge_base_url" label="BGE-M3 服务地址">
          <Input />
        </Form.Item>
        <Form.Item name="embedding_model_id" label="Embedding 模型标识">
          <Input />
        </Form.Item>
        <Form.Item name="reranker_base_url" label="Reranker 服务地址">
          <Input />
        </Form.Item>
        <Form.Item name="llm_base_url" label="LLM OpenAI-compatible 地址">
          <Input />
        </Form.Item>
        <Form.Item name="llm_model" label="LLM 模型">
          <Input />
        </Form.Item>
        <Button type="primary" htmlType="submit">
          保存并刷新模型连接
        </Button>
      </Form>
    </Card>
  );
}
