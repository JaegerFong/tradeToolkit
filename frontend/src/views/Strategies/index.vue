<template>
  <div class="strategy-page">
    <section class="toolbar">
      <div>
        <h1>策略工具</h1>
        <p>结构化 Markdown 策略、每日筛选复盘、股票池与信号级回测。</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新增策略</el-button>
    </section>

    <section class="content-grid">
      <div class="strategy-list">
        <el-table :data="strategies" v-loading="loading" height="520" @row-click="selectStrategy">
          <el-table-column prop="name" label="策略" min-width="180" />
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'enabled' ? 'success' : 'info'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="校验" width="100">
            <template #default="{ row }">
              <el-tag :type="row.validation_status === 'valid' ? 'success' : 'danger'">{{ row.validation_status }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="detail-panel">
        <template v-if="selected">
          <div class="detail-head">
            <div>
              <h2>{{ selected.name }}</h2>
              <p>v{{ selected.version }} · {{ selected.schedule?.cron || '未配置调度' }}</p>
            </div>
            <div class="actions">
              <el-button :icon="Check" @click="validateSelected">校验</el-button>
              <el-button type="primary" :icon="VideoPlay" @click="runSelected">运行</el-button>
            </div>
          </div>

          <el-alert
            v-if="selected.errors?.length"
            type="error"
            :closable="false"
            :title="selected.errors.join('；')"
          />
          <el-alert
            v-else-if="selected.warnings?.length"
            type="warning"
            :closable="false"
            :title="selected.warnings.join('；')"
          />

          <el-tabs v-model="activeTab">
            <el-tab-pane label="运行结果" name="run">
              <div v-if="currentRun" class="run-summary">
                <el-progress :percentage="currentRun.progress?.percent || 0" />
                <el-alert
                  v-if="currentRun.error"
                  type="error"
                  :closable="false"
                  :title="currentRun.error"
                />
                <p>{{ currentRun.summary || currentRun.progress?.message }}</p>
                <el-input v-if="currentRun.daily_review" v-model="currentRun.daily_review" type="textarea" :rows="4" readonly />
                <el-input v-if="currentRun.next_day_plan" v-model="currentRun.next_day_plan" type="textarea" :rows="4" readonly />
              </div>
              <el-table :data="results" height="360">
                <el-table-column prop="code" label="代码" width="110" />
                <el-table-column prop="name" label="名称" width="120" />
                <el-table-column prop="total_score" label="评分" width="90" sortable />
                <el-table-column prop="status" label="状态" width="120" />
                <el-table-column prop="close" label="收盘" width="90" />
                <el-table-column prop="stop_loss" label="止损" width="90" />
                <el-table-column prop="entry_reason" label="入选原因" min-width="260" show-overflow-tooltip />
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="股票池" name="pool">
              <el-button :icon="Refresh" @click="loadPool">刷新股票池</el-button>
              <el-table :data="pool" height="420">
                <el-table-column prop="code" label="代码" width="110" />
                <el-table-column prop="name" label="名称" width="120" />
                <el-table-column prop="status" label="状态" width="130" />
                <el-table-column prop="last_score" label="评分" width="90" />
                <el-table-column prop="tracking_days" label="跟踪天数" width="100" />
                <el-table-column prop="entry_reason" label="原因" min-width="260" show-overflow-tooltip />
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="回测" name="backtest">
              <div class="backtest-form">
                <el-date-picker v-model="backtestRange" type="daterange" start-placeholder="开始日期" end-placeholder="结束日期" value-format="YYYY-MM-DD" />
                <el-input-number v-model="holdingDays" :min="1" :max="30" />
                <el-button type="primary" :icon="DataAnalysis" @click="startBacktest">开始回测</el-button>
              </div>
              <div v-if="backtest" class="run-summary">
                <el-progress :percentage="backtest.progress?.percent || 0" />
                <p>{{ backtest.summary || backtest.progress?.message }}</p>
                <pre>{{ JSON.stringify(backtest.metrics, null, 2) }}</pre>
              </div>
            </el-tab-pane>
          </el-tabs>
        </template>
        <el-empty v-else description="选择或新增一个策略" />
      </div>
    </section>

    <el-dialog v-model="dialogVisible" title="策略 Markdown" width="860px">
      <el-form label-position="top">
        <el-form-item label="策略名称">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="Markdown">
          <el-input v-model="form.markdown" type="textarea" :rows="18" />
        </el-form-item>
        <el-form-item>
          <el-checkbox v-model="form.enabled">启用每日任务</el-checkbox>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveStrategy">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, DataAnalysis, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { strategiesApi, type StrategyBacktest, type StrategyDetail, type StrategyRun, type StrategyRunResult, type StrategySummary } from '@/api/strategies'

const loading = ref(false)
const strategies = ref<StrategySummary[]>([])
const selected = ref<StrategyDetail | null>(null)
const currentRun = ref<StrategyRun | null>(null)
const results = ref<StrategyRunResult[]>([])
const pool = ref<any[]>([])
const backtest = ref<StrategyBacktest | null>(null)
const activeTab = ref('run')
const dialogVisible = ref(false)
const backtestRange = ref<[string, string] | null>(null)
const holdingDays = ref(3)
const form = reactive({ name: '', markdown: '', enabled: false })

const loadStrategies = async () => {
  loading.value = true
  try {
    strategies.value = await strategiesApi.list()
  } finally {
    loading.value = false
  }
}

const selectStrategy = async (row: StrategySummary) => {
  selected.value = await strategiesApi.get(row.strategy_id)
  results.value = []
  currentRun.value = null
  await loadPool()
}

const openCreate = () => {
  form.name = '强趋势股量化交易系统'
  form.markdown = '# 强趋势股量化交易系统\n\n请粘贴规范策略 Markdown。'
  form.enabled = false
  dialogVisible.value = true
}

const saveStrategy = async () => {
  selected.value = await strategiesApi.create({ name: form.name, markdown: form.markdown, enabled: form.enabled })
  dialogVisible.value = false
  ElMessage.success('策略已保存')
  await loadStrategies()
}

const validateSelected = async () => {
  if (!selected.value) return
  const result = await strategiesApi.validate(selected.value.strategy_id)
  if (result.status === 'valid') {
    ElMessage.success('校验通过')
  } else {
    ElMessage.error(result.errors?.join('；') || '校验失败')
  }
}

const runSelected = async () => {
  if (!selected.value) return
  currentRun.value = await strategiesApi.createRun(selected.value.strategy_id)
  ElMessage.success('策略任务已提交')
  pollRun()
}

const pollRun = async () => {
  if (!selected.value || !currentRun.value) return
  const sid = selected.value.strategy_id
  const rid = currentRun.value.run_id
  const timer = window.setInterval(async () => {
    currentRun.value = await strategiesApi.getRun(sid, rid)
    if (['completed', 'failed', 'cancelled', 'data_incomplete'].includes(currentRun.value.status)) {
      window.clearInterval(timer)
      const resp = await strategiesApi.listResults(sid, rid)
      results.value = resp.items
      await loadPool()
      if (currentRun.value.status === 'failed' || currentRun.value.status === 'data_incomplete') {
        ElMessage.error(currentRun.value.error || currentRun.value.progress?.message || '策略任务执行失败')
      }
    }
  }, 2500)
}

const loadPool = async () => {
  if (!selected.value) return
  pool.value = await strategiesApi.listPool(selected.value.strategy_id)
}

const startBacktest = async () => {
  if (!selected.value || !backtestRange.value) {
    ElMessage.warning('请选择策略和回测日期范围')
    return
  }
  backtest.value = await strategiesApi.createBacktest(selected.value.strategy_id, {
    start_date: backtestRange.value[0],
    end_date: backtestRange.value[1],
    holding_days: holdingDays.value
  })
  const sid = selected.value.strategy_id
  const bid = backtest.value.backtest_id
  const timer = window.setInterval(async () => {
    backtest.value = await strategiesApi.getBacktest(sid, bid)
    if (['completed', 'failed', 'cancelled'].includes(backtest.value.status)) {
      window.clearInterval(timer)
    }
  }, 2500)
}

onMounted(loadStrategies)
</script>

<style scoped>
.strategy-page {
  padding: 20px;
}

.toolbar,
.detail-head,
.backtest-form {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.toolbar h1,
.detail-head h2 {
  margin: 0;
  font-size: 22px;
}

.toolbar p,
.detail-head p {
  margin: 6px 0 0;
  color: var(--el-text-color-secondary);
}

.content-grid {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(520px, 1.6fr);
  gap: 16px;
  margin-top: 16px;
}

.strategy-list,
.detail-panel {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 14px;
  background: var(--el-bg-color);
}

.actions {
  display: flex;
  gap: 8px;
}

.run-summary {
  display: grid;
  gap: 10px;
  margin-bottom: 12px;
}

pre {
  margin: 0;
  padding: 12px;
  overflow: auto;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}

@media (max-width: 980px) {
  .content-grid,
  .toolbar,
  .detail-head,
  .backtest-form {
    display: block;
  }

  .detail-panel {
    margin-top: 16px;
  }
}
</style>
