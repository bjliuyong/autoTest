# 故障修复：`vendor/dsh` 的 junction 在传输中丢失

## 现象

在目标机上跑 `--relink` 和自检，出现：

```text
[relink] D:\code\autoTest\vendor\dsh 下共 0 个链接
[relink] 重建 0 个，无需改动 0 个，清理死链 0 个，异常 0 个
[verify] bundle 内 DSH 自检：失败（exit=1）
[verify] stderr: Error: Cannot find package
  'D:\code\autoTest\vendor\dsh\apps\cli\node_modules\@deepseek-ai\dsh-app-boot\index.js'
```

## 原因

`vendor/dsh` 里是 pnpm 的链接布局：`node_modules/@deepseek-ai/*` 等位置有 **6205 个
junction**（Windows 的 reparse point）。

zip 压缩、镜像拷贝、部分同步/网盘工具**不保留 junction**，会把它降级成空目录。
于是包解析链全断，node 报"找不到某个包"，而 `--relink` 扫描到 **0 个链接** ——
它只能改写**已有**链接的目标，救不回已经丢掉的链接。

> 对比：健康的 bundle 里 `vendor\dsh\apps\cli\node_modules\@deepseek-ai\cordis`
> 的 `LinkType=Junction`、有 8 个子项；坏掉的是空目录、0 个子项。

## 怎么修（二选一）

### 方案 A（推荐）

**目标机已经装好 DSH 时，根本不需要 `vendor/dsh`** —— 用命令模式直接调它：

```yaml
dsh:
  command: dsh        # PATH 里的 dsh（npm 全局装的一般是 dsh.cmd，程序会自动经 cmd /c 调用）
  cli_js: null        # 保持 null = 命令模式；填了 cli_js 才会用包里的那份
```

```bat
dsh --help                             :: 有输出即可
python tools\verify_offline.py         :: 应为 [ OK ] 全部通过
```

可选：删掉 `vendor\dsh` 和 `vendor\node`，省 1.6 GB。

> 模型、API 地址、API Key 都不用配 —— 沿用 DSH 自己的配置，本程序不覆盖。

### 方案 B（确实需要包内自带 DSH）

用链接清单重建，**不需要重传 1.5 GB**：

1. 把 `links-fix.zip` 里的
   `tools\prepare_offline_bundle.py` 与 `vendor\links.json`
   覆盖到目标机对应位置；
2. 执行：

```bat
python tools\prepare_offline_bundle.py --restore-links
```

期望输出：

```text
[restore] 清单 6205 条：新建 6205 个，已正确 0 个，目标缺失 0 个，失败 0 个
[verify] bundle 内 DSH 自检：通过（exit=0）
```

3. 配置里改用脚本模式（`cli_js` 优先于 `command`）：

```yaml
dsh:
  cli_js: ./vendor/dsh/apps/cli/lib/bin.js
  node_exe: ./vendor/node/node.exe
```

4. 再跑一次 `python tools\verify_offline.py` 确认。

> `links.json` 里的目标全是**相对路径**，重建后整棵树放哪个盘、哪个目录都行，
> **不需要再跑 `--relink`**。

## 以后怎么传才不会坏

| 方式 | 结果 |
| --- | --- |
| ❌ zip / 镜像 / 网盘同步 `vendor/dsh` | junction 丢失，DSH 起不来 |
| ✅ 目标机已装 DSH | **不传 `vendor/dsh`**，用命令模式（推荐） |
| ✅ `python tools/make_archive.py --with-dsh` | 包里带 `links.json`，目标机 `--restore-links` 重建 |
| ⚠️ 资源管理器直接拷目录 | 会把 junction 跟随成真实目录（能用，但体积膨胀、且有环风险，不推荐） |

`python tools/make_archive.py`（默认）不含 `vendor/dsh`，约 2 MB，普通传输即可。
