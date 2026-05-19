<template>
  <el-card class="unified-sync-panel" shadow="never">
    <template #header>
      <div class="panel-header">
        <h3><el-icon><Connection /></el-icon> 数据同步</h3>
        <el-tag v-if="isRunning" type="warning" size="small" effect="dark">同步中</el-tag>
        <el-tag v-else-if="lastStatus === 'completed'" type="success" size="small">就绪</el-tag>
        <el-tag v-else type="info" size="small">就绪</el-tag>
      </div>
    </template>

    <!-- 运行中的任务进度（页面刷新后可恢复） -->
    <div v-if="isRunning" class="active-job">
      <div class="job-header">
        <div class="job-info">
          <el-tag :type="jobTypeTag" size="small" effect="dark">{{ jobTypeLabel }}</el-tag>
          <span class="job-elapsed" v-if="elapsedText">⏱ {{ elapsedText }}</span>
        </div>
        <el-button type="danger" size="small" @click="cancelSync" plain>取消任务</el-button>
      </div>

      <el-progress
        :percentage="activeProgress"
        :stroke-width="20"
        :status="activeProgress === 100 ? 'success' : undefined"
        class="job-progress"
      >
        <span class="progress-text">{{ activeProgress }}%</span>
      </el-progress>

      <div class="job-step-detail">
        <el-icon><Loading /></el-icon>
        <span>{{ activeJob.current_step || activeJob.progress_message || '处理中...' }}</span>
      </div>

      <div v-if="activeJob.total_steps" class="step-meter">
        <span
          v-for="i in activeJob.total_steps"
          :key="i"
          class="step-block"
          :class="{
            done: i <= (activeJob.completed_steps || 0),
            active: i === (activeJob.completed_steps || 0) + 1,
          }"
        >
          {{ i <= (activeJob.completed_steps || 0) ? '✅' : i === (activeJob.completed_steps || 0) + 1 ? '🔄' : '⬜' }}
        </span>
      </div>

      <div v-if="activeJob.results && activeJob.results.length > 0" class="step-results">
        <div v-for="(r, i) in activeJob.results" :key="i" class="result-row">
          <el-tag :type="r.success ? 'success' : 'danger'" size="small" effect="plain">
            {{ r.success ? '完成' : '失败' }}
          </el-tag>
          <span class="result-step">{{ r.step }}</span>
          <span v-if="r.error" class="result-error">{{ r.error }}</span>
        </div>
      </div>
    </div>

    <!-- 同步配置表单（任务运行中时禁用） -->
    <el-form :model="form" label-width="100px" :disabled="isRunning" class="sync-form">
      <!-- 快速预设 -->
      <el-form-item label="快速预设">
        <el-radio-group v-model="preset" @change="applyPreset" size="small">
          <el-radio-button value="basics">基础信息</el-radio-button>
          <el-radio-button value="strategy">策略数据</el-radio-button>
          <el-radio-button value="full">全量历史</el-radio-button>
          <el-radio-button value="custom">自定义</el-radio-button>
        </el-radio-group>
        <div class="preset-desc">{{ presetDescription }}</div>
      </el-form-item>

      <!-- 自定义选项（仅自定义模式显示） -->
      <template v-if="preset === 'custom'">
        <el-form-item label="数据类型">
          <el-checkbox-group v-model="form.sync_items">
            <el-checkbox v-for="item in syncItems" :key="item.key" :label="item.key">{{ item.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="数据源">
          <el-checkbox-group v-model="form.data_sources">
            <el-checkbox v-for="s in dataSources" :key="s.key" :label="s.key">{{ s.label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>

        <el-form-item label="同步模式">
          <el-radio-group v-model="form.mode">
            <el-radio-button value="incremental">增量同步</el-radio-button>
            <el-radio-button value="full">全量同步</el-radio-button>
            <el-radio-button value="date_range">指定区间</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.mode === 'date_range'" label="日期区间">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            format="YYYY-MM-DD"
            value-format="YYYY-MM-DD"
          />
        </el-form-item>

        <el-form-item label="股票范围">
          <el-radio-group v-model="form.symbol_scope">
            <el-radio-button value="all">全市场</el-radio-button>
            <el-radio-button value="favorites">仅自选股</el-radio-button>
            <el-radio-button value="custom">指定代码</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item v-if="form.symbol_scope === 'custom'" label="股票代码">
          <el-input v-model="symbolsText" type="textarea" :rows="2" placeholder="逗号或换行分隔，如：000001,600000" />
        </el-form-item>
      </template>

      <!-- 启动按钮 -->
      <el-form-item>
        <el-button type="primary" size="large" :loading="starting" :disabled="isRunning" @click="startSync">
          <el-icon><VideoPlay /></el-icon>
          {{ startLabel }}
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, VideoPlay, Loading } from '@element-plus/icons-vue'
import {
  getSyncItems, runUnifiedSync, getUnifiedSyncStatus, cancelUnifiedSync,
  getRunningSyncJobs, clearSyncCache, stopAkshareInit,
  runStockBasicsSync, getSyncStatus as getBasicsSyncStatus,
  startStrategyDataSync as startStratSync, getAkshareInitializationStatus,
  type SyncItem, type UnifiedSyncResult,
} from '@/api/sync'

const syncItems = ref<SyncItem[]>([])
const dataSources = ref<{ key: string; label: string }[]>([])

const preset = ref('basics')
const form = reactive({
  sync_items: ['basic_info', 'historical'] as string[],
  data_sources: ['tushare', 'akshare'] as string[],
  mode: 'incremental' as string,
  symbol_scope: 'all' as string,
})
const dateRange = ref<[string, string] | null>(null)
const symbolsText = ref('')

const jobStartTime = ref<number>(0)
const elapsedText = ref('')

const jobTypeLabel = computed(() => {
  const labels: Record<string, string> = { basics: '基础信息同步', strategy: '策略数据同步', full: '全量历史同步', custom: '自定义同步', unified: '统一同步', akshare_init: 'AKShare 初始化' }
  return labels[activeJob.value?.type || preset.value] || '数据同步'
})
const jobTypeTag = computed(() => {
  const types: Record<string, string> = { basics: 'primary', strategy: 'success', full: 'warning', custom: 'info', unified: '', akshare_init: 'success' }
  return types[activeJob.value?.type || preset.value] || ''
})

const isRunning = ref(false)
const starting = ref(false)
const activeJob = ref<any>(null)
const lastStatus = ref('')
let pollTimer: any = null

const presetDescription = computed(() => {
  const m: Record<string, string> = {
    basics: '同步股票基础信息（多数据源），适用于首次部署或定期更新',
    strategy: '同步基础信息 + 近1年历史日线（AKShare），适用于策略工具数据准备',
    full: '全量同步：基础信息、历史日/周/月线、财务数据、实时行情',
    custom: '自由选择数据类型、数据源、同步模式和股票范围',
  }
  return m[preset.value] || ''
})

const activeProgress = computed(() => {
  if (!activeJob.value) return 0
  if (activeJob.value.total_steps) {
    const pct = Math.round((activeJob.value.completed_steps / activeJob.value.total_steps) * 100)
    // 正在执行中且进度 < 5% 时显示 5%，避免"卡在 0%"的错觉
    return isRunning.value && pct < 5 ? 5 : pct
  }
  return activeJob.value.progress || 0
})

const startLabel = computed(() => {
  const labels: Record<string, string> = {
    basics: '开始同步基础信息',
    strategy: '开始同步策略数据',
    full: '开始全量同步',
    custom: '开始自定义同步',
  }
  return labels[preset.value] || '开始同步'
})

const PRESETS: Record<string, any> = {
  basics: { sync_items: ['basic_info'], data_sources: ['tushare', 'akshare'], mode: 'incremental', symbol_scope: 'all' },
  strategy: { sync_items: ['basic_info', 'historical'], data_sources: ['akshare'], mode: 'incremental', symbol_scope: 'all' },
  full: { sync_items: ['basic_info', 'historical', 'weekly', 'monthly', 'financial', 'quotes'], data_sources: ['tushare', 'akshare'], mode: 'full', symbol_scope: 'all' },
}

function applyPreset() {
  const p = PRESETS[preset.value]
  if (p) {
    form.sync_items = [...p.sync_items]
    form.data_sources = [...p.data_sources]
    form.mode = p.mode
    form.symbol_scope = p.symbol_scope
  }
}

onMounted(async () => {
  try {
    const res = await getSyncItems()
    if (res.success) {
      syncItems.value = res.data.items
      dataSources.value = res.data.sources
    }
  } catch {}
  applyPreset()
  checkRunningJobs()
})

onUnmounted(() => { if (pollTimer) clearTimeout(pollTimer) })

async function checkRunningJobs() {
  try {
    const res = await getRunningSyncJobs()
    if (res.success && res.data?.running_tasks?.length > 0) {
      const task = res.data.running_tasks[0]
      activeJob.value = task
      isRunning.value = true
      if (task.type === 'unified' && task.job_id) {
        currentJobId = task.job_id
        pollUnifiedProgress()
      } else if (task.type === 'akshare_init') {
        pollAkshareInitProgress()
      }
    }
  } catch {}
}

let currentJobId = ''

async function startSync() {
  starting.value = true
  try {
    if (preset.value === 'basics') {
      await startBasicsSync()
    } else if (preset.value === 'strategy') {
      await startStrategySync()
    } else {
      await startUnifiedSync()
    }
  } catch (e: any) {
    ElMessage.error(`启动失败: ${e.message}`)
  } finally {
    starting.value = false
  }
}

function startElapsedTimer() {
  jobStartTime.value = Date.now()
  elapsedText.value = ''
  const update = () => { if (isRunning.value) { const s = Math.floor((Date.now() - jobStartTime.value) / 1000); elapsedText.value = `${Math.floor(s/60)}分${s%60}秒`; setTimeout(update, 1000) } }
  update()
}

async function startBasicsSync() {
  await runStockBasicsSync({ force: false })
  ElMessage.success('基础信息同步已启动')
  isRunning.value = true
  startElapsedTimer()
  activeJob.value = { type: 'basics', current_step: '同步基础信息...' }
  pollBasicsProgress()
}

async function startStrategySync() {
  await startStratSync({ historical_days: 365, force: false })
  ElMessage.success('策略数据同步已启动')
  isRunning.value = true
  startElapsedTimer()
  activeJob.value = { type: 'akshare_init', current_step: '同步策略数据...' }
  pollAkshareInitProgress()
}

async function startUnifiedSync() {
  const symbols = form.symbol_scope === 'custom'
    ? symbolsText.value.split(/[,\n]/).map(s => s.trim()).filter(Boolean) : undefined
  const res = await runUnifiedSync({
    sync_items: form.sync_items,
    data_sources: form.data_sources,
    mode: form.mode,
    start_date: form.mode === 'date_range' && dateRange.value ? dateRange.value[0] : undefined,
    end_date: form.mode === 'date_range' && dateRange.value ? dateRange.value[1] : undefined,
    symbol_scope: form.symbol_scope,
    symbols,
  })
  if (res.success) {
    currentJobId = res.data.job_id
    isRunning.value = true
    startElapsedTimer()
    activeJob.value = { type: 'unified', job_id: currentJobId, total_steps: 0, completed_steps: 0, current_step: '启动中...' }
    ElMessage.success('统一同步已启动')
    pollUnifiedProgress()
  }
}

function pollBasicsProgress() {
  pollTimer = setTimeout(async () => {
    try {
      const res = await getBasicsSyncStatus()
      const data = res.data
      if (data?.status === 'running') {
        activeJob.value = {
          ...activeJob.value,
          current_step: '同步基础信息...',
          progress: data.total ? Math.round(((data.inserted + data.updated) / data.total) * 100) : 0,
        }
        pollBasicsProgress()
      } else {
        finishJob(data?.status === 'completed' || data?.status === 'success')
      }
    } catch { pollBasicsProgress() }
  }, 3000)
}

function pollAkshareInitProgress() {
  pollTimer = setTimeout(async () => {
    try {
      const res = await getAkshareInitializationStatus()
      const data = res.data
      if (data?.is_running) {
        const p = data.progress
        activeJob.value = {
          ...activeJob.value,
          current_step: data.current_task || '处理中...',
          total_steps: p?.total_steps || 0,
          completed_steps: p?.completed_steps || 0,
        }
        pollAkshareInitProgress()
      } else {
        finishJob(data?.result?.success !== false)
      }
    } catch { pollAkshareInitProgress() }
  }, 3000)
}

function pollUnifiedProgress() {
  pollTimer = setTimeout(async () => {
    try {
      const res = await getUnifiedSyncStatus(currentJobId)
      const data = res.data
      if (data?.status === 'running') {
        activeJob.value = { ...activeJob.value, ...data }
        pollUnifiedProgress()
      } else {
        activeJob.value = { ...activeJob.value, ...data }
        finishJob(data?.status === 'completed')
      }
    } catch { pollUnifiedProgress() }
  }, 2000)
}

function finishJob(success: boolean) {
  lastStatus.value = success ? 'completed' : 'failed'
  isRunning.value = false
  if (pollTimer) clearTimeout(pollTimer)
}

async function cancelSync() {
  try {
    const jobType = activeJob.value?.type || preset.value
    if (jobType === 'basics') {
      // 基础信息同步通过清缓存来中断
      await clearSyncCache()
    } else if (jobType === 'akshare_init' || jobType === 'strategy') {
      // AKShare 策略/初始化同步
      await stopAkshareInit()
    } else if (currentJobId) {
      await cancelUnifiedSync(currentJobId)
    }
    ElMessage.warning('已请求取消')
  } catch (e: any) {
    ElMessage.error('取消失败: ' + (e.message || '未知错误'))
  } finally {
    finishJob(false)
  }
}
</script>

<style scoped lang="scss">
.unified-sync-panel {
  .panel-header { display: flex; align-items: center; gap: 12px; h3 { margin: 0; display: flex; align-items: center; gap: 8px; } }
  .active-job {
    margin-bottom: 16px; padding: 16px; background: var(--el-color-warning-light-9); border-radius: 8px;
    .job-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .job-info { display: flex; align-items: center; gap: 8px; }
    .job-elapsed { font-size: 13px; color: var(--el-text-color-secondary); }
    .job-progress { margin: 8px 0; .progress-text { font-weight: bold; font-size: 14px; } }
    .job-step-detail { display: flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 14px; color: var(--el-text-color-primary); }
    .step-meter { display: flex; gap: 4px; margin-top: 10px; .step-block { font-size: 16px; opacity: 0.4; &.done { opacity: 1; } &.active { opacity: 1; animation: pulse 1s infinite; } } }
    .step-results { margin-top: 10px; }
    .result-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; font-size: 13px; .result-step { flex: 1; } .result-error { color: var(--el-color-danger); font-size: 12px; } }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
  }
  .sync-form { margin-top: 4px; }
  .preset-desc { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 6px; }
}
</style>
