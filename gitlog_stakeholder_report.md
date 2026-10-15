# Gitlog 贡献者角色分类报告（Stakeholders 视角）

## 1. 分析目标
基于 `gitlog.csv`，识别项目中的主要贡献者，并参考 Stakeholders 分类中的以下五类角色进行尝试性归类：
- Developers
- Maintainers
- Testers
- System Administrators
- Production Engineers

参考分类来源：
- https://www.viewpoints-and-perspectives.info/home/stakeholders/

## 2. 数据范围与处理方式
- 数据文件：`gitlog.csv`
- 分析日期：2026-04-15
- 总提交数：3892
- 贡献者总数：249
- 贡献者别名归并：`Juan Font Alonso` 归并到 `Juan Font`
- 机器人账号处理：`github-actions[bot]` 不纳入“主要贡献者”名单

### 分类方法（启发式）
由于提交日志只有 message 文本，无法直接获得岗位信息，本报告采用关键词启发式单标签分类（每条提交只归入一个角色，按优先级匹配）：
1. Testers：test, integration, flaky, race 等
2. Production Engineers：ci, workflow, docker, nix, flake, build, release 等
3. System Administrators：systemd, deploy, service reload, healthcheck, operator 等
4. Maintainers：changelog, docs, faq, refactor, lint, format, upgrade 等
5. Developers：其余默认归为功能开发/缺陷修复

## 3. 全量提交分类结果

| 角色 | 提交数 | 占比 |
|---|---:|---:|
| Developers | 2625 | 67.44% |
| Maintainers | 535 | 13.75% |
| Testers | 421 | 10.82% |
| Production Engineers | 297 | 7.63% |
| System Administrators | 14 | 0.36% |

说明：该仓库提交以功能开发为主，其次是维护与测试；运维与生产工程相关提交相对集中在少数贡献者与特定阶段。

## 4. 主要贡献者（按提交量 Top 10）

| 贡献者 | 提交数 | 角色分布（D/M/T/SA/PE） | 主角色判断 |
|---|---:|---|---|
| Kristoffer Dalby | 1823 | 1253 / 190 / 260 / 3 / 117 | Developers（兼顾 Testers、Maintainers、PE） |
| Juan Font | 1039 | 768 / 101 / 104 / 0 / 66 | Developers（兼顾 Maintainers、Testers） |
| Adrien Raffin-Caboisse | 119 | 80 / 28 / 8 / 0 / 3 | Developers |
| Florian Preinstorfer | 103 | 68 / 28 / 1 / 4 / 2 | Developers + Maintainers（含少量 SA） |
| Ward Vandewege | 47 | 29 / 3 / 11 / 0 / 4 | Developers + Testers |
| ohdearaugustin | 40 | 13 / 11 / 0 / 0 / 16 | Production Engineers |
| nblock | 33 | 26 / 4 / 1 / 1 / 1 | Developers（兼顾 Maintainers） |
| Csaba Sarkadi | 25 | 15 / 6 / 4 / 0 / 0 | Developers |
| Jiang Zhu | 25 | 16 / 6 / 1 / 0 / 2 | Developers |
| Benjamin George Roberts | 16 | 10 / 2 / 4 / 0 / 0 | Developers + Testers |

## 5. 按 Stakeholder 角色归类的主要贡献者与工作

### 5.1 Developers
典型职责：构建系统能力、修复核心逻辑、实现功能演进。

主要贡献者与工作示例：
- Kristoffer Dalby
  - `0acf09bd` policy/v2: add localpart:*@domain SSH user compilation
  - `cb3b6949` auth: generalise auth flow and introduce AuthVerdict
  - `30338441` app: switch from gorilla to chi mux
- Juan Font
  - `c906aaf9` Allow to remove forced tags of a node
  - `a244eabd` Ephemeral keys can now be reusable and non-reusable
  - `bd047928` Move pprof to metrics router
- Adrien Raffin-Caboisse
  - `babf9470` fix(acl): fix issue with groups in excludeCorretlyTaggedNodes
  - `c90e8624` fix(grpc): add more checks for tag validation
- nblock
  - `e73b2a9f` Ensure that a username starts with a letter

### 5.2 Maintainers
典型职责：维护演进质量、文档与变更说明、代码整洁性与升级路径。

主要贡献者与工作示例：
- Florian Preinstorfer
  - `9baa795d` Update docs for auth-id changes
  - `cfb308b4` Add FAQ entry to migrate back to default IP prefixes
  - `d556df1c` Extend upgrade guide with backup instructions
- Kristoffer Dalby
  - `84adda22` doc: add CHANGELOG entries for SSH check and auth commands
  - `43afeedd` all: apply golangci-lint 2.9.0 fixes
- Juan Font
  - `c027ef0f` Added changelog for 0.22.3
  - `c7db99d6` Update changelog + prepare for 0.22.2
- nblock
  - `fa619ea9` Fix CHANGELOG for autogroup:member and autogroup:tagged

### 5.3 Testers
典型职责：构建/修复测试、提升回归与兼容性保障。

主要贡献者与工作示例：
- Kristoffer Dalby
  - `5c449db1` ci: regenerate test-integration.yaml for TestSSHLocalpart
  - `2be94ce1` integration: add TestSSHLocalpart integration test
  - `65880ecb` nix: disable external DERP URL fetch in VM test
- Juan Font
  - `68503581` Add test stage to docs
  - `1d2b4bca` Remove legacy DERP tests
- Ward Vandewege
  - `f9187bdf` ... Update docs accordingly. Fix integration test failure.
  - `1d9954d8` Fix integration test
- Benjamin George Roberts
  - `60cc9ddb` Add test for autoApprovers feature
  - `7761a7b2` fix autoapprover test following tagged authkey change

### 5.4 System Administrators
典型职责：运行系统、服务配置与操作可用性。

该类提交量整体较少，但有明确贡献：
- Kristoffer Dalby
  - `a5afe4bd` Add more capabilities for systemd
  - `307cfc33` add systemd enable to postinstall script
- Florian Preinstorfer
  - `91461402` Add headscale-operator
  - `2a1392fb` Add healthcheck to container docs
- nblock
  - `35bfe7ce` Add support for service reload and sync service file

### 5.5 Production Engineers
典型职责：构建、发布、CI/CD、运行环境与工程效率。

主要贡献者与工作示例：
- Kristoffer Dalby
  - `37c6a9e3` nix: sync module options and descriptions with upstream nixpkgs
  - `25ccb5a1` build: update golangci-lint and gopls in flake
  - `0f972946` ci: regenerate integration test workflow
- Juan Font
  - `f73354b4` Create default sock path in Docker
  - `4c8f8c6a` Ditch distroless for Docker image
- ohdearaugustin
  - `34d26117` Speedup docker container build
  - `3db88d27` github/workflows: init renovatebot
  - `c6bc9fff` workflows/release: fix docker debug tags
- Adrien Raffin-Caboisse
  - `852dc0f4` feat: add golangci-lint in nix develop
  - `0445f404` fix: pin version of golangci-lint in GA

## 6. 结论
- 项目呈现明显的“核心开发者驱动”结构，前两位贡献者（Kristoffer Dalby、Juan Font）承担了绝大部分开发与演进工作。
- 维护与测试工作持续且密集，说明项目具备较稳定的质量治理与发布节奏。
- Production Engineers 角色在 CI/CD、Docker、Nix/Flake 生态中较突出，体现了较强的工程化投入。
- System Administrators 相关提交占比低，但关键能力（systemd、service reload、healthcheck、operator）已有覆盖。

## 7. 局限性与改进建议
- 本报告基于 commit message 文本做启发式分类，可能低估“同一提交的多重角色”。
- 部分提交信息较泛化（如 merge、apply suggestions），对角色识别信息量有限。
- 若要提高精度，建议结合：
  - 修改文件路径（如 `integration/`, `.github/workflows/`, `packaging/`, `docs/`）
  - PR 标签与 Review 记录
  - 发布周期与事故复盘信息
