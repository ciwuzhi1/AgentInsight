# frontend：给完全没写过前端的人的说明

这个目录是 AgentInsight 的"门面"——你在浏览器里看到的那个页面，全部由这里的几个文件变成现实。下面用尽量少的专业术语，把它讲明白。

## 一、React 和 Next.js 是什么

**React** 解决一个核心问题：**让界面自动跟着数据变**。

想象一块连着传感器的电子告示牌：传感器（数据）一变，告示牌上的字自动刷新，不用人拿抹布去擦。React 就是这套"数据变 → 界面自动变"的机制。

**Next.js** 是 React 的**官方全家桶**。React 本身只负责"画界面"，而一个真实网站还需要路由（每个网址对应哪页）、打包构建、性能优化等杂事——Next.js 把这些全都预先配好。本项目选 Next.js 一句话：**React 管界面跟着数据变，Next 把工程里脏活累活都包了**。

## 二、组件与状态：页面是一块"积木"，状态是它的"命"

- **组件（Component）**：像乐高积木。页面上传面板、提问框、爬虫面板，各自是一个独立组件，各自管自己的事。
- **状态（State）**：组件的"记忆"。最常用的工具是 `useState`：

```tsx
const [query, setQuery] = useState(""); // 开局记忆是空字符串
setQuery("按地区统计总销售额");          // 改记忆，界面自动跟着变
```

记住一个核心观念：**界面是状态的投影**。你不直接去改界面（不去手动改 DOM），只改状态，React 会自动把界面刷成状态该有的样子。就像改了剧本，舞台上的戏自动跟着重演。

## 三、`"use client"` 是什么意思

`page.tsx` 第一行写着 `"use client"`，意思是：**这个页面要在浏览器里运行、可以交互**（点击按钮、监听 SSE 都发生在你的电脑上）。不加这行的话，代码默认在服务器上运行、渲染成静态 HTML——那就点不了按钮了。本项目是个交互式工作台，所以必须加。

## 四、每个配置文件管什么

| 文件 | 类比 | 作用 |
|---|---|---|
| `package.json` | 购物清单 | 记录项目需要哪些第三方库（next、react、echarts…）和可执行的命令（`npm run dev` 等） |
| `node_modules/` | 按清单买回来的货 | `npm install` 把清单上的库全部下载到这个文件夹，代码从这里取货 |
| `next.config.mjs` | 框架行为开关面板 | 调整 Next.js 的运行行为（本项目只需最简配置） |
| `tsconfig.json` | TypeScript 的翻译设置 | TS 是"带语法检查的 JS"，能在写代码时就抓出拼写和类型错误；此文件规定怎么检查、怎么翻译成 JS |
| `postcss.config.mjs` + Tailwind | CSS 加工流水线 | Tailwind 是"用类名写样式"的工具，PostCSS 负责在构建时把它加工成浏览器认识的 CSS |

## 五、Tailwind：样式即类名

传统 CSS 要先在样式表里写 `.button { background: blue; }`，再在 HTML 上挂 class。Tailwind 直接把样式拆成一个个"原子类"写在标签上：

```tsx
<button className="rounded-lg bg-sky-600 px-5 py-2 text-white">
  上传
</button>
```

读法：`rounded-lg` 圆角大一点，`bg-sky-600` 天蓝色底，`px-5 py-2` 内边距，`text-white` 白字。**不用来回跳文件，看着类名就能想象出样子**，深色主题下也方便统一微调。

## 六、ECharts 怎么被 React 驱动

ECharts 是百度的开源画图库，它自己管理一个 `<canvas>` 画布，React "够不着"里面的像素。所以用 `useRef` 拿到画布容器、用 `useEffect` 把数据"塞"给图表实例：

```tsx
const divRef = useRef(null);          // 指向页面上的一个 div
useEffect(() => {
  const chart = echarts.init(divRef.current); // 在 div 上初始化图表
  chart.setOption({ xAxis: {...}, series: [...] }); // 把数据喂进去
  return () => chart.dispose();       // 组件卸载时拆掉图表，防内存泄漏
}, []);
```

规则很简单：**数据一变 → useEffect 再调一次 `setOption` → 图表自动重画**。本项目在 `ChartBox` 组件里就是这么做的（还顺带监听了窗口缩放 `resize`）。

## 七、SSE 和普通请求的区别

- **普通请求（fetch）**：一问一答。你问一句，服务器想多久都想，想完一次全告诉你。期间你只能干等。
- **SSE（Server-Sent Events）**：服务器推送的"流水"。连接建立后，服务器每做完一步就主动推一条消息过来，像看直播弹幕，而不是等信。

```tsx
const es = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
es.onmessage = (ev) => {
  const event = JSON.parse(ev.data); // agent_start / engine / sql / final ...
};
```

Agent 分析数据要走"选引擎 → 生成 SQL → 执行 → 校验"好几步，前端正是靠 SSE 把每一步实时画进"执行时间线"。收到 `final` 或 `error` 就 `es.close()` 挂断。

## 八、页面区块一览

1. **数据集上传**：选 CSV → `POST /api/datasets` → 展示行数、大小、推荐引擎、列结构表。
2. **提问**：输入框 + 4 个示例按钮 → `POST /api/tasks` 拿到 task_id → 立刻开 SSE 听事件。
3. **执行时间线**：每个事件实时渲染一行（Agent 名 + 耗时 + 状态徽标、引擎徽标、SQL 代码块、错误红条）。
4. **结果卡片**：一句话结论 + ECharts 柱状/折线图 + 数据表（前 50 行）+ 可折叠的 SQL。
5. **爬虫面板**：抓招聘 JD 入库，一键"导出 CSV 给 Spark"。

## 九、怎么跑起来

```bash
cd frontend
npm install     # 按购物清单买货（首次）
npm run dev     # 启动开发服务器
```

浏览器打开 **http://localhost:3000** 即可。需要后端（端口 8000）同时在跑；如果后端地址不同，可设置环境变量 `NEXT_PUBLIC_API_BASE` 指过去。

**常见坑**：页面报"网络错误"多半是后端没启动；改了代码没生效，刷新即可（dev 模式自动热更新，一般不需要手动重启）。
