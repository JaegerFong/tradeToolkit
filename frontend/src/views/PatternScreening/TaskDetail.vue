<template>
  <div class="pattern-task-page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">技术形态选股任务</h1>
        <p class="page-description">
          查看选股过程、结果列表与入选分析。结果仅供研究与教育用途，不构成投资建议。
        </p>
      </div>
      <div class="header-right">
        <el-button @click="refreshAll" :loading="loading">刷新</el-button>
        <el-button type="danger" plain @click="cancelTask" :disabled="!canCancel">取消任务</el-button>
      </div>
    </div>

    <el-card shadow="never" class="status-card">
      <div class="status-grid">
        <div class="status-item">
          <div class="label">状态</div>
          <div class="value">
            <el-tag :type="statusTagType">{{ task?.status ?? '-' }}</el-tag>
          </div>
        </div>
        <div class="status-item">
          <div class="label">进度</div>
          <div class="value">
            <el-progress :percentage="task?.progress?.percent ?? 0" :stroke-width="10" />
            <div class="sub">{{ task?.progress?.message ?? '' }}</div>
          </div>
        </div>
        <div class="status-item">
          <div class="label">扫描/候选/入选</div>
          <div class="value mono">
            {{ task?.stats?.total_scanned ?? 0 }} / {{ task?.stats?.candidate_count ?? 0 }} / {{ task?.stats?.selected_count ?? 0 }}
          </div>
        </div>
        <div class="status-item">
          <div class="label">摘要</div>
          <div class="value">{{ task?.summary ?? '-' }}</div>
        </div>
      </div>
      <div v-if="task?.error" class="error-box">
        {{ task.error }}
      </div>
    </el-card>

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card shadow="never" class="events-card">
          <template #header>
            <div class="card-header">
              <span>过程事件</span>
              <span class="muted">最多显示 {{ eventsLimit }} 条</span>
            </div>
          </template>

          <el-timeline class="events-timeline">
            <el-timeline-item
              v-for="(ev, idx) in events"
              :key="idx"
              :timestamp="formatTs(ev.timestamp)"
              :type="timelineType(ev.step)"
              :hollow="ev.step !== 'finalize'"
            >
              <div class="ev-title">{{ ev.title }}</div>
              <div class="ev-msg">{{ ev.message }}</div>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card shadow="never" class="results-card">
          <template #header>
            <div class="card-header">
              <span>入选股票</span>
              <div class="right">
                <el-input v-model="keyword" placeholder="搜索代码/名称" clearable style="width: 220px" />
              </div>
            </div>
          </template>

          <el-table :data="filteredResults" v-loading="loadingResults" style="width: 100%" height="520">
            <el-table-column prop="code" label="编码" width="110" />
            <el-table-column prop="name" label="名称" min-width="120" />
            <el-table-column prop="price" label="价格" width="90">
              <template #default="{ row }">{{ row.price?.toFixed?.(2) ?? row.price }}</template>
            </el-table-column>
            <el-table-column prop="change_amount" label="涨跌额" width="90">
              <template #default="{ row }">
                <span :class="chgClass(row.change_amount)">{{ formatSigned(row.change_amount) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="pct_chg" label="涨跌幅" width="90">
              <template #default="{ row }">
                <span :class="chgClass(row.pct_chg)">{{ formatSigned(row.pct_chg) }}%</span>
              </template>
            </el-table-column>
            <el-table-column prop="market_cap" label="市值" width="120">
              <template #default="{ row }">{{ formatMarketCap(row.market_cap) }}</template>
            </el-table-column>
            <el-table-column prop="pattern_name" label="形态" width="100" />
            <el-table-column prop="recommendation_score" label="推荐值" width="90" />
            <el-table-column label="操作" width="180" fixed="right">
              <template #default="{ row }">
                <el-button size="small" @click="openDetail(row)">详情</el-button>
                <el-button size="small" type="primary" plain @click="goStock(row.code)">股票详情</el-button>
              </template>
            </el-table-column>
          </el-table>

          <div class="pager">
            <el-pagination
              background
              layout="prev, pager, next"
              :page-size="pageSize"
              :current-page="page"
              :total="total"
              @current-change="onPageChange"
            />
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-drawer v-model="detailVisible" size="520px" title="入选分析" destroy-on-close>
      <template v-if="detail">
        <div class="drawer-head">
          <div class="title">{{ detail.name }}（{{ detail.code }}）</div>
          <div class="tags">
            <el-tag>{{ detail.pattern_type }}</el-tag>
            <el-tag type="success">形态 {{ detail.pattern_score }}</el-tag>
            <el-tag type="warning">推荐 {{ detail.recommendation_score }}</el-tag>
          </div>
        </div>

        <el-divider>形态拆解</el-divider>
        <div class="kv" v-for="(v, k) in detail.pattern_breakdown" :key="k">
          <div class="k">{{ k }}</div>
          <div class="v">{{ v }}</div>
        </div>

        <el-divider>入选分析</el-divider>
        <div class="text">{{ detail.analysis }}</div>

        <el-divider>走势预期</el-divider>
        <div class="text">{{ detail.trend_expectation }}</div>

        <el-divider>交易建议</el-divider>
        <div class="kv">
          <div class="k">买入区间</div>
          <div class="v">{{ detail.buy_price_range?.[0] }} ~ {{ detail.buy_price_range?.[1] }}</div>
        </div>
        <div class="kv">
          <div class="k">仓位建议</div>
          <div class="v">{{ detail.position_suggestion }}</div>
        </div>
        <div class="kv">
          <div class="k">止损位</div>
          <div class="v">{{ detail.stop_loss }}</div>
        </div>

        <el-divider>风险点</el-divider>
        <el-tag v-for="(r, i) in detail.risk_points" :key="i" type="danger" class="tag">{{ r }}</el-tag>

        <el-divider>失效条件</el-divider>
        <el-tag v-for="(r, i) in detail.invalid_conditions" :key="i" type="info" class="tag">{{ r }}</el-tag>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  patternScreeningApi,
  type PatternEvent,
  type PatternResultDetail,
  type PatternResultListItem,
  type PatternTaskResp
} from '@/api/patternScreening'

const route = useRoute()
const router = useRouter()
const taskId = computed(() => String(route.params.taskId || ''))

const loading = ref(false)
const loadingResults = ref(false)
const task = ref<PatternTaskResp | null>(null)
const events = ref<PatternEvent[]>([])

const pageSize = 50
const page = ref(1)
const total = ref(0)
const results = ref<PatternResultListItem[]>([])
const keyword = ref('')
const eventsLimit = 200

const detailVisible = ref(false)
const detail = ref<PatternResultDetail | null>(null)

const canCancel = computed(() => {
  const s = task.value?.status
  return s === 'queued' || s === 'running'
})

const statusTagType = computed(() => {
  switch (task.value?.status) {
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    case 'cancelled':
      return 'info'
    case 'running':
      return 'warning'
    default:
      return 'info'
  }
})

const filteredResults = computed(() => {
  const k = keyword.value.trim()
  if (!k) return results.value
  return results.value.filter((x) => x.code.includes(k) || x.name.includes(k))
})

function formatTs(ts: string) {
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return ts
  }
}

function chgClass(v: number) {
  if (v > 0) return 'pos'
  if (v < 0) return 'neg'
  return 'neu'
}

function formatSigned(v: number) {
  if (v > 0) return `+${v}`
  return `${v}`
}

function formatMarketCap(v: number) {
  if (!v) return '-'
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}万亿`
  if (v >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (v >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return String(v)
}

function timelineType(step: string) {
  if (step === 'finalize') return 'success'
  if (step === 'cancel') return 'info'
  if (step === 'llm_review') return 'warning'
  return 'primary'
}

async function fetchTask() {
  task.value = await patternScreeningApi.getTask(taskId.value)
}

async function fetchEvents() {
  events.value = await patternScreeningApi.listEvents(taskId.value, eventsLimit)
}

async function fetchResults() {
  loadingResults.value = true
  try {
    const offset = (page.value - 1) * pageSize
    const resp = await patternScreeningApi.listResults(taskId.value, pageSize, offset)
    results.value = resp.items
    total.value = resp.total
  } finally {
    loadingResults.value = false
  }
}

async function refreshAll() {
  loading.value = true
  try {
    await Promise.all([fetchTask(), fetchEvents(), fetchResults()])
  } catch (e: any) {
    ElMessage.error(e?.message ?? '刷新失败')
  } finally {
    loading.value = false
  }
}

function onPageChange(p: number) {
  page.value = p
  fetchResults()
}

async function openDetail(row: PatternResultListItem) {
  try {
    detail.value = await patternScreeningApi.getResultDetail(taskId.value, row.code)
    detailVisible.value = true
  } catch (e: any) {
    ElMessage.error(e?.message ?? '获取详情失败')
  }
}

function goStock(code: string) {
  // code 格式类似 000001.SZ / 600000.SH，股票详情路由需要 6位数字
  const pure = code.split('.')[0]
  router.push({ name: 'StockDetail', params: { code: pure } })
}

async function cancelTask() {
  if (!canCancel.value) return
  try {
    const resp = await patternScreeningApi.cancelTask(taskId.value)
    if (resp.success) ElMessage.success('已提交取消请求')
    await refreshAll()
  } catch (e: any) {
    ElMessage.error(e?.message ?? '取消失败')
  }
}

let timer: any = null
onMounted(async () => {
  await refreshAll()
  timer = setInterval(async () => {
    const s = task.value?.status
    if (s === 'queued' || s === 'running') {
      await refreshAll()
    }
  }, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped lang="scss">
.pattern-task-page {
  .status-card {
    margin-bottom: 16px;
  }

  .status-grid {
    display: grid;
    grid-template-columns: 160px 1fr 220px 1fr;
    gap: 16px;
  }

  .status-item .label {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-bottom: 6px;
  }

  .status-item .value {
    font-size: 14px;
  }

  .status-item .sub {
    margin-top: 6px;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  }

  .error-box {
    margin-top: 12px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--el-color-danger-light-9);
    color: var(--el-color-danger);
  }

  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .muted {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }

  .pager {
    margin-top: 12px;
    display: flex;
    justify-content: flex-end;
  }

  .pos {
    color: var(--el-color-success);
  }
  .neg {
    color: var(--el-color-danger);
  }
  .neu {
    color: var(--el-text-color-regular);
  }

  .drawer-head .title {
    font-weight: 600;
    font-size: 16px;
    margin-bottom: 8px;
  }
  .drawer-head .tags {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .kv {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 10px;
    padding: 6px 0;
  }
  .kv .k {
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
  .kv .v {
    color: var(--el-text-color-primary);
    font-size: 13px;
    line-height: 1.5;
  }
  .text {
    color: var(--el-text-color-primary);
    font-size: 13px;
    line-height: 1.65;
  }
  .tag {
    margin: 4px 6px 0 0;
  }
}
</style>
