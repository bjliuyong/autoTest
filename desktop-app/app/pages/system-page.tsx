import { useState } from 'react'
import { KeyRound, Plus, Shield, Trash2, UserPlus, Users, FileClock } from 'lucide-react'
import { Badge } from '@/app/components/ui/badge'
import { Button } from '@/app/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/app/components/ui/card'
import { Input } from '@/app/components/ui/input'
import { Label } from '@/app/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/app/components/ui/tabs'
import { conveyor } from '@/conveyor/client'
import type { AuditLogEntry, PermissionDef, RoleItem, UserProfile } from '@/lib/autotest/types'

interface SystemPageProps {
  currentUser: UserProfile
  users: UserProfile[]
  roles: RoleItem[]
  allPermissions: PermissionDef[]
  auditLogs: AuditLogEntry[]
  onRefresh: () => void
}

function fmtDate(iso: string | null) {
  if (!iso) return '从未登录'
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return iso
  }
}

export function SystemPage({
  currentUser,
  users,
  roles,
  allPermissions,
  auditLogs,
  onRefresh,
}: SystemPageProps) {
  const canUser = currentUser.roleCode === 'admin' || currentUser.permissions.includes('system:user')
  const canRole = currentUser.roleCode === 'admin' || currentUser.permissions.includes('system:role')
  const canAudit = currentUser.roleCode === 'admin' || currentUser.permissions.includes('system:audit')

  // Create User state
  const [newUsername, setNewUsername] = useState('')
  const [newNickname, setNewNickname] = useState('')
  const [newPassword, setNewPassword] = useState('123456')
  const [newRoleCode, setNewRoleCode] = useState('tester')
  const [newDept, setNewDept] = useState('质量保障部')
  const [msg, setMsg] = useState('')

  // Create Role state
  const [roleCode, setRoleCode] = useState('')
  const [roleName, setRoleName] = useState('')
  const [roleDesc, setRoleDesc] = useState('')
  const [rolePerms, setRolePerms] = useState<string[]>(['dashboard:view'])

  const handleCreateUser = async () => {
    if (!newUsername.trim()) return
    setMsg('')
    try {
      await conveyor.autotest.createUser({
        username: newUsername.trim(),
        nickname: newNickname.trim() || newUsername.trim(),
        password: newPassword || '123456',
        roleCode: newRoleCode,
        department: newDept,
      })
      setNewUsername('')
      setNewNickname('')
      setMsg('用户创建成功')
      onRefresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : '创建用户失败')
    }
  }

  const handleToggleUserStatus = async (u: UserProfile) => {
    await conveyor.autotest.updateUser({
      id: u.id,
      status: u.status === 'active' ? 'disabled' : 'active',
    })
    onRefresh()
  }

  const handleChangeUserRole = async (u: UserProfile, nextRole: string) => {
    await conveyor.autotest.updateUser({ id: u.id, roleCode: nextRole })
    onRefresh()
  }

  const handleResetPassword = async (u: UserProfile) => {
    await conveyor.autotest.resetUserPassword({ id: u.id, password: '123456' })
    setMsg(`已将用户 ${u.username} 的密码重置为 123456`)
  }

  const handleDeleteUser = async (u: UserProfile) => {
    await conveyor.autotest.deleteUser(u.id)
    onRefresh()
  }

  const handleCreateRole = async () => {
    if (!roleCode.trim() || !roleName.trim()) return
    setMsg('')
    try {
      await conveyor.autotest.createRole({
        code: roleCode.trim(),
        name: roleName.trim(),
        description: roleDesc.trim(),
        permissions: rolePerms,
      })
      setRoleCode('')
      setRoleName('')
      setRoleDesc('')
      setRolePerms(['dashboard:view'])
      setMsg('角色创建成功')
      onRefresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : '创建角色失败')
    }
  }

  const handleToggleRolePerm = async (role: RoleItem, permCode: string) => {
    if (role.code === 'admin') return
    const exists = role.permissions.includes(permCode)
    const next = exists ? role.permissions.filter((p) => p !== permCode) : [...role.permissions, permCode]
    await conveyor.autotest.updateRole({ code: role.code, permissions: next })
    onRefresh()
  }

  const handleDeleteRole = async (code: string) => {
    await conveyor.autotest.deleteRole(code)
    onRefresh()
  }

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">系统管理（RBAC 权限中心）</h1>
          <p className="text-xs text-muted-foreground">
            类似 RuoYi 的用户管理、角色权限分配与操作审计日志，所有变更实时同步至共享后端
          </p>
        </div>
        {msg && <Badge variant="secondary" className="text-xs text-brand">{msg}</Badge>}
      </div>

      <Tabs defaultValue={canUser ? 'users' : canRole ? 'roles' : 'audit'} className="w-full">
        <TabsList>
          {canUser && (
            <TabsTrigger value="users" className="gap-1.5 text-xs">
              <Users className="size-3.5" /> 用户管理 ({users.length})
            </TabsTrigger>
          )}
          {canRole && (
            <TabsTrigger value="roles" className="gap-1.5 text-xs">
              <Shield className="size-3.5" /> 角色与权限 ({roles.length})
            </TabsTrigger>
          )}
          {canAudit && (
            <TabsTrigger value="audit" className="gap-1.5 text-xs">
              <FileClock className="size-3.5" /> 操作审计日志
            </TabsTrigger>
          )}
        </TabsList>

        {/* Tab 1: User Management */}
        {canUser && (
          <TabsContent value="users" className="mt-4 space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="flex items-center gap-1.5 text-sm font-medium">
                  <UserPlus className="size-4 text-brand" /> 新增团队成员账号
                </CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-3 md:grid-cols-6">
                <div className="space-y-1">
                  <Label className="text-[11px]">用户名</Label>
                  <Input
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    placeholder="如 zhangsan"
                    className="h-8 text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">姓名 / 昵称</Label>
                  <Input
                    value={newNickname}
                    onChange={(e) => setNewNickname(e.target.value)}
                    placeholder="如 张三"
                    className="h-8 text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">初始密码</Label>
                  <Input
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="h-8 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">部门</Label>
                  <Input
                    value={newDept}
                    onChange={(e) => setNewDept(e.target.value)}
                    className="h-8 text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-[11px]">角色</Label>
                  <select
                    value={newRoleCode}
                    onChange={(e) => setNewRoleCode(e.target.value)}
                    className="flex h-8 w-full rounded-md border border-input bg-background px-2 text-xs"
                  >
                    {roles.map((r) => (
                      <option key={r.code} value={r.code}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex items-end">
                  <Button
                    size="sm"
                    onClick={handleCreateUser}
                    className="h-8 w-full gap-1 bg-brand text-xs text-white hover:bg-brand-hover"
                  >
                    <Plus className="size-3.5" /> 添加用户
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">全部系统用户</CardTitle>
                <CardDescription className="text-xs">支持修改角色、启停账号、重置密码（默认 123456）</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-border text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">用户名 / 姓名</th>
                        <th className="px-3 py-2 font-medium">部门</th>
                        <th className="px-3 py-2 font-medium">角色分配</th>
                        <th className="px-3 py-2 font-medium">状态</th>
                        <th className="px-3 py-2 font-medium">最近登录</th>
                        <th className="py-2 pl-3 text-right font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {users.map((u) => (
                        <tr key={u.id} className="hover:bg-secondary/40">
                          <td className="py-2.5 pr-3">
                            <div className="font-mono font-semibold">{u.username}</div>
                            <div className="text-[11px] text-muted-foreground">{u.nickname}</div>
                          </td>
                          <td className="px-3 py-2.5 text-muted-foreground">{u.department || '-'}</td>
                          <td className="px-3 py-2.5">
                            <select
                              disabled={u.username === 'admin'}
                              value={u.roleCode}
                              onChange={(e) => handleChangeUserRole(u, e.target.value)}
                              className="h-7 rounded border border-input bg-background px-2 text-xs disabled:opacity-60"
                            >
                              {roles.map((r) => (
                                <option key={r.code} value={r.code}>
                                  {r.name}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td className="px-3 py-2.5">
                            <Badge
                              variant="outline"
                              className={
                                u.status === 'active'
                                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500'
                                  : 'border-rose-500/30 bg-rose-500/10 text-rose-500'
                              }
                            >
                              {u.status === 'active' ? '正常' : '已禁用'}
                            </Badge>
                          </td>
                          <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                            {fmtDate(u.lastLoginAt)}
                          </td>
                          <td className="py-2.5 pl-3 text-right">
                            <div className="inline-flex items-center gap-1">
                              {u.username !== 'admin' && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 px-2 text-xs"
                                  onClick={() => handleToggleUserStatus(u)}
                                >
                                  {u.status === 'active' ? '禁用' : '启用'}
                                </Button>
                              )}
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-7 gap-1 px-2 text-xs"
                                onClick={() => handleResetPassword(u)}
                                title="重置密码为 123456"
                              >
                                <KeyRound className="size-3" /> 重置密码
                              </Button>
                              {u.username !== 'admin' && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-7 px-2 text-rose-500 hover:bg-rose-500/10 hover:text-rose-500"
                                  onClick={() => handleDeleteUser(u)}
                                >
                                  <Trash2 className="size-3.5" />
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* Tab 2: Roles & Permissions */}
        {canRole && (
          <TabsContent value="roles" className="mt-4 space-y-4">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-medium">新建自定义角色</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
                  <Input
                    value={roleCode}
                    onChange={(e) => setRoleCode(e.target.value)}
                    placeholder="角色标识（如 qa_lead）"
                    className="h-8 font-mono text-xs"
                  />
                  <Input
                    value={roleName}
                    onChange={(e) => setRoleName(e.target.value)}
                    placeholder="角色名称（如 测试组长）"
                    className="h-8 text-xs"
                  />
                  <Input
                    value={roleDesc}
                    onChange={(e) => setRoleDesc(e.target.value)}
                    placeholder="角色描述"
                    className="h-8 text-xs"
                  />
                  <Button
                    size="sm"
                    onClick={handleCreateRole}
                    className="h-8 gap-1 bg-brand text-xs text-white hover:bg-brand-hover"
                  >
                    <Plus className="size-3.5" /> 创建角色
                  </Button>
                </div>
                <div className="flex flex-wrap gap-2">
                  {allPermissions.map((p) => {
                    const checked = rolePerms.includes(p.code)
                    return (
                      <button
                        key={p.code}
                        type="button"
                        onClick={() =>
                          setRolePerms((prev) =>
                            checked ? prev.filter((x) => x !== p.code) : [...prev, p.code]
                          )
                        }
                        className={`rounded-md border px-2.5 py-1 text-[11px] transition-colors ${
                          checked
                            ? 'border-brand bg-brand/15 text-brand'
                            : 'border-border bg-secondary/40 text-muted-foreground'
                        }`}
                      >
                        {checked ? '✓ ' : ''}
                        {p.label}
                      </button>
                    )
                  })}
                </div>
              </CardContent>
            </Card>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {roles.map((role) => (
                <Card key={role.code}>
                  <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
                    <div>
                      <CardTitle className="flex items-center gap-2 text-sm font-medium">
                        {role.name}
                        <Badge variant="outline" className="font-mono text-[10px]">
                          {role.code}
                        </Badge>
                        {role.isSystem && (
                          <Badge variant="secondary" className="text-[10px]">
                            系统内置
                          </Badge>
                        )}
                      </CardTitle>
                      <CardDescription className="mt-1 text-xs">{role.description || '-'}</CardDescription>
                    </div>
                    {!role.isSystem && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-rose-500 hover:bg-rose-500/10"
                        onClick={() => handleDeleteRole(role.code)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    )}
                  </CardHeader>
                  <CardContent>
                    <div className="flex flex-wrap gap-1.5">
                      {allPermissions.map((p) => {
                        const active = role.code === 'admin' || role.permissions.includes(p.code)
                        return (
                          <button
                            key={p.code}
                            type="button"
                            disabled={role.code === 'admin'}
                            onClick={() => handleToggleRolePerm(role, p.code)}
                            className={`rounded-md border px-2 py-1 text-[11px] transition-colors disabled:cursor-default ${
                              active
                                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-500'
                                : 'border-border bg-background text-muted-foreground hover:border-border-bright'
                            }`}
                          >
                            {active ? '✓ ' : '+ '}
                            {p.label}
                          </button>
                        )
                      })}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </TabsContent>
        )}

        {/* Tab 3: Audit Logs */}
        {canAudit && (
          <TabsContent value="audit" className="mt-4">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium">团队操作审计日志（最近 {auditLogs.length} 条）</CardTitle>
                <CardDescription className="text-xs">
                  自动记录所有成员的登录、启停测试、修改配置及用户/角色变更行为
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="max-h-[460px] overflow-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-border text-muted-foreground">
                        <th className="py-2 pr-3 font-medium">时间</th>
                        <th className="px-3 py-2 font-medium">操作人</th>
                        <th className="px-3 py-2 font-medium">模块</th>
                        <th className="px-3 py-2 font-medium">动作</th>
                        <th className="px-3 py-2 font-medium">详情</th>
                        <th className="py-2 pl-3 text-right font-medium">来源 IP</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {auditLogs.map((a) => (
                        <tr key={a.id} className="hover:bg-secondary/30">
                          <td className="py-2 pr-3 font-mono text-[11px] text-muted-foreground">
                            {fmtDate(a.createdAt)}
                          </td>
                          <td className="px-3 py-2 font-mono font-medium">{a.username}</td>
                          <td className="px-3 py-2">
                            <Badge variant="outline" className="font-mono text-[10px]">
                              {a.module}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 font-medium">{a.action}</td>
                          <td className="px-3 py-2 text-muted-foreground">{a.detail}</td>
                          <td className="py-2 pl-3 text-right font-mono text-[11px] text-muted-foreground">
                            {a.ip || '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
