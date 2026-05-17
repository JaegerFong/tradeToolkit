<template>
  <div class="strategy-data-sync">
    <el-card class="sync-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <el-icon class="header-icon"><TrendCharts /></el-icon>
            <span>策略行情数据</span>
          </div>
          <el-button size="small" :loading="loading" @click="refreshAll">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
        </div>
      </template>

      <div v-loading="loading" class="card-content">
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
          class="section-gap"
        />

        <div class="summary-grid">
          <div class="summary-item">
            <div class="summary-value">{{ formatNumber(status?.basic_info.total_count) }}</div>
            <div class="summary-label">基础信息</div>
          </div>
          <div class="summary-item">
            <div class="summary-value">{{ formatNumber(status?.historical_daily.total_count) }}</div>
            <div class="summary-label">日线记录</div>
          </div>
          <div class="summary-item">
            <div class="summary-value">{{ formatNumber(status?.historical_daily.symbol_count) }}</div>
            <div class="summary-label">覆盖股票</div>
          </div>
          <div class="summary-item">
            <div class="summary-value compact">{{ dataRangeText }}</div>
            <div class="summary-label">日线周期</div>
          </div>
        </div>

        <div class="detail-list">
          <div class="detail-row">
            <span class="detail-label">最早交易日</span>
            <span>{{ status?.historical_daily.earliest_trade_date || '-' }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">最新交易日</span>
            <span>{{ status?.historical_daily.latest_trade_date || '-' }}</span>
          </div>
          <div class="detail-row">
            <span class="detail-label">最近更新时间</span>
            <span>{{ formatTime(status?.historical_daily.latest_update || status?.basic_info.latest_update) }}</span>
          </div>
        </div>

        <div v-if="taskStatus?.is_running" class="running-section">
          <el-progress
            :percentage="taskProgress"
            :stroke-width="8"
            status="warning"
          />
          <div class="running-text">
            {{ taskStatus.progress?.current_step || '同步中' }}
            <span v-if="taskStatus.duration">，已运行 {{ formatDuration(taskStatus.duration) }}</span>
          </div>
        </div>

        <div v-else-if="taskStatus?.result" class="result-section">
          <el-alert
            :title="taskStatus.result.success ? '最近一次同步完成' : '最近一次同步失败'"
            :description="resultDescription"
            :type="taskStatus.result.success ? 'success' : 'error'"
            :closable="false"
            show-icon
          />
        </div>

        <div class="sync-form">
          <el-form :model="form" label-width="96px">
            <el-form-item label="同步周期">
              <el-segmented
                v-model="form.days"
                :options="periodOptions"
                :disabled="taskStatus?.is_running"
              />
            </el-form-item>

            <el-form-item v-if="form.days === 0" label="自定义天数">
              <el-input-number
                v-model="form.customDays"
                :min="1"
                :max="3650"
                :step="30"
                controls-position="right"
                :disabled="taskStatus?.is_running"
              />
            </el-form-item>

            <el-form-item label="强制同步">
              <el-switch
                v-model="form.force"
                active-text="已有数据也继续"
                inactive-text="已有数据则跳过"
                :disabled="taskStatus?.is_running"
              />
            </el-form-item>
          </el-form>

          <div class="action-row">
            <el-button
              type="primary"
              size="large"
              :loading="starting || taskStatus?.is_running"
              :disabled="taskStatus?.is_running"
              @click="startSync"
            >
              <el-icon><Refresh /></el-icon>
              {{ taskStatus?.is_running ? '同步中...' : `同步${selectedPeriodText}` }}
            </el-button>
            <el-button size="large" :loading="loading" @click="refreshAll">
              查看最新状态
            </el-button>
          </div>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, TrendCharts } from '@element-plus/icons-vue'
import {
  getAkshareDatabaseStatus,
  getAkshareInitializationStatus,
  startStrategyDataSync,
  type AkshareDatabaseStatus,
  type AkshareInitializationStatus
} from '@/api/sync'

const loading = ref(false)
const starting = ref(false)
const error = ref('')
const status = ref<AkshareDatabaseStatus | null>(null)
const taskStatus = ref<AkshareInitializationStatus | null>(null)
const pollTimer = ref<NodeJS.Timeout | null>(null)

const periodOptions = [
  { label: '近1年', value: 365 },
  { label: '近2年', value: 730 },
  { label: '近3年', value: 1095 },
  { label: '近5年', value: 1825 },
  { label: '自定义', value: 0 }
]

const form = reactive({
  days: 365,
  customDays: 365,
  force: true
})

const selectedDays = computed(() => form.days === 0 ? form.customDays : form.days)

const selectedPeriodText = computed(() => {
  const found = periodOptions.find(item => item.value === form.days)
  if (found && found.value !== 0) return found.label
  return `${selectedDays.value}天`
})

const dataRangeText = computed(() => {
  const daily = status.value?.historical_daily
  if (!daily?.earliest_trade_date || !daily?.latest_trade_date) return '-'
  return `${daily.earliest_trade_date} 至 ${daily.latest_trade_date}`
})

const taskProgress = computed(() => {
  const progress = taskStatus.value?.progress
  if (!progress?.total_steps) return 0
  return Math.round(((progress.completed_steps || 0) / progress.total_steps) * 100)
})

const resultDescription = computed(() => {
  const result = taskStatus.value?.result
  if (!result) return ''
  if (result.error) return result.error
  const summary = result.data_summary || {}
  return `基础信息 ${formatNumber(summary.basic_info_count)} 条，日线 ${formatNumber(summary.daily_records)} 条`
})

const formatNumber = (value?: number) => {
  if (value === undefined || value === null) return '0'
  return value.toLocaleString('zh-CN')
}

const formatTime = (value?: string) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

const formatDuration = (seconds: number) => {
  if (seconds < 60) return `${Math.round(seconds)}秒`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return `${minutes}分${rest}秒`
}

const fetchStatus = async () => {
  const response = await getAkshareDatabaseStatus()
  if (response.success) {
    status.value = response.data
  } else {
    throw new Error(response.message || '获取数据状态失败')
  }
}

const fetchTaskStatus = async () => {
  const response = await getAkshareInitializationStatus()
  if (response.success) {
    taskStatus.value = response.data
  } else {
    throw new Error(response.message || '获取同步任务状态失败')
  }
}

const refreshAll = async () => {
  try {
    loading.value = true
    error.value = ''
    await Promise.all([fetchStatus(), fetchTaskStatus()])
    if (taskStatus.value?.is_running) {
      startPolling()
    }
  } catch (err: any) {
    error.value = err.message || '刷新失败'
  } finally {
    loading.value = false
  }
}

const startSync = async () => {
  try {
    starting.value = true
    const response = await startStrategyDataSync({
      historical_days: selectedDays.value,
      force: form.force
    })
    if (!response.success) {
      throw new Error(response.message || '启动同步失败')
    }
    ElMessage.success(`已启动${selectedPeriodText.value}策略数据同步`)
    await fetchTaskStatus()
    startPolling()
  } catch (err: any) {
    ElMessage.error(err.message || '启动同步失败')
  } finally {
    starting.value = false
  }
}

const startPolling = () => {
  stopPolling()
  pollTimer.value = setInterval(async () => {
    await fetchTaskStatus()
    if (!taskStatus.value?.is_running) {
      stopPolling()
      await fetchStatus()
      const ok = taskStatus.value?.result?.success
      ElMessage({
        message: ok ? '策略数据同步完成' : '策略数据同步结束，请查看结果',
        type: ok ? 'success' : 'warning',
        duration: 6000,
        showClose: true
      })
    }
  }, 5000)
}

const stopPolling = () => {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

onMounted(refreshAll)
onUnmounted(stopPolling)
</script>

<style scoped lang="scss">
.strategy-data-sync {
  .sync-card {
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;

      .header-title {
        display: flex;
        align-items: center;
        font-weight: 600;
      }

      .header-icon {
        margin-right: 8px;
        color: var(--el-color-primary);
      }
    }
  }

  .section-gap {
    margin-bottom: 16px;
  }

  .summary-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 16px;
  }

  .summary-item {
    min-height: 82px;
    padding: 14px;
    border: 1px solid var(--el-border-color-light);
    border-radius: 8px;
    background: var(--el-fill-color-lighter);

    .summary-value {
      min-height: 28px;
      margin-bottom: 4px;
      color: var(--el-color-primary);
      font-size: 22px;
      font-weight: 700;
      line-height: 1.25;

      &.compact {
        color: var(--el-text-color-primary);
        font-size: 13px;
        font-weight: 600;
        overflow-wrap: anywhere;
      }
    }

    .summary-label {
      color: var(--el-text-color-secondary);
      font-size: 13px;
    }
  }

  .detail-list {
    margin-bottom: 18px;
    border-top: 1px solid var(--el-border-color-lighter);
  }

  .detail-row {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid var(--el-border-color-lighter);
    font-size: 14px;

    .detail-label {
      color: var(--el-text-color-secondary);
    }
  }

  .running-section,
  .result-section {
    margin-bottom: 18px;
  }

  .running-text {
    margin-top: 8px;
    color: var(--el-text-color-regular);
    font-size: 13px;
  }

  .sync-form {
    .action-row {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }
  }
}

@media (max-width: 960px) {
  .strategy-data-sync {
    .summary-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
}
</style>
