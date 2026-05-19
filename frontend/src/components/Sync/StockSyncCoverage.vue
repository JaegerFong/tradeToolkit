<template>
  <div class="stock-sync-coverage">
    <el-card class="coverage-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <div class="header-title">
            <el-icon class="header-icon"><List /></el-icon>
            <span>已同步股票明细</span>
          </div>
          <el-button size="small" :loading="loading" @click="fetchCoverage">
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
        </div>
      </template>

      <div class="toolbar">
        <el-input
          v-model="filters.keyword"
          clearable
          placeholder="代码或名称"
          style="width: 220px"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="filters.source" clearable placeholder="基础数据源" style="width: 150px" @change="search">
          <el-option label="AKShare" value="akshare" />
          <el-option label="Tushare" value="tushare" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="search">查询</el-button>
      </div>

      <div v-if="summary" class="summary-grid">
        <div class="summary-item">
          <div class="summary-value">{{ formatNumber(summary.basic_stock_count) }}</div>
          <div class="summary-label">基础股票</div>
        </div>
        <div class="summary-item">
          <div class="summary-value">{{ formatNumber(periodSummary('daily')?.symbol_count) }}</div>
          <div class="summary-label">日线覆盖</div>
        </div>
        <div class="summary-item">
          <div class="summary-value">{{ formatNumber(summary.financial_stock_count) }}</div>
          <div class="summary-label">财务覆盖</div>
        </div>
        <div class="summary-item">
          <div class="summary-value">{{ formatNumber(summary.quote_stock_count) }}</div>
          <div class="summary-label">行情快照</div>
        </div>
      </div>

      <el-table :data="items" v-loading="loading" height="520" border>
        <el-table-column prop="code" label="代码" width="92" fixed />
        <el-table-column prop="name" label="名称" min-width="110" fixed />
        <el-table-column prop="industry" label="行业" min-width="110" />
        <el-table-column label="基础" width="145">
          <template #default="{ row }">
            <div class="cell-stack">
              <el-tag size="small" type="success">{{ row.basic.source || '已同步' }}</el-tag>
              <span>{{ formatDate(row.basic.latest_update) }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="日线" min-width="190">
          <template #default="{ row }">
            <PeriodCell :period="row.historical.daily" />
          </template>
        </el-table-column>
        <el-table-column label="周线" min-width="190">
          <template #default="{ row }">
            <PeriodCell :period="row.historical.weekly" />
          </template>
        </el-table-column>
        <el-table-column label="月线" min-width="190">
          <template #default="{ row }">
            <PeriodCell :period="row.historical.monthly" />
          </template>
        </el-table-column>
        <el-table-column label="财务" width="170">
          <template #default="{ row }">
            <div v-if="row.financial.count" class="cell-stack">
              <el-tag size="small" type="success">{{ row.financial.count }} 条</el-tag>
              <span>最新 {{ row.financial.latest_report_period || '-' }}</span>
            </div>
            <el-tag v-else size="small" type="info">未同步</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="行情快照" width="150">
          <template #default="{ row }">
            <div v-if="row.quotes.exists" class="cell-stack">
              <el-tag size="small" type="success">{{ row.quotes.source || '已同步' }}</el-tag>
              <span>{{ row.quotes.trade_date || formatDate(row.quotes.latest_update) }}</span>
            </div>
            <el-tag v-else size="small" type="info">未同步</el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-row">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @size-change="fetchCoverage"
          @current-change="fetchCoverage"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, reactive, ref } from 'vue'
import { ElTag } from 'element-plus'
import { List, Refresh, Search } from '@element-plus/icons-vue'
import {
  getStockSyncCoverage,
  type StockSyncCoverageItem,
  type StockSyncCoveragePeriod,
  type StockSyncCoverageSummary
} from '@/api/sync'

const loading = ref(false)
const items = ref<StockSyncCoverageItem[]>([])
const summary = ref<StockSyncCoverageSummary | null>(null)
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)

const filters = reactive({
  keyword: '',
  source: ''
})

const PeriodCell = defineComponent({
  props: {
    period: {
      type: Object as () => StockSyncCoveragePeriod | undefined,
      default: undefined
    }
  },
  setup(props) {
    return () => {
      if (!props.period?.count) {
        return h(ElTag, { size: 'small', type: 'info' }, () => '未同步')
      }
      return h('div', { class: 'cell-stack' }, [
        h(ElTag, { size: 'small', type: 'success' }, () => `${props.period?.count || 0} 条`),
        h('span', {}, `${props.period.earliest_trade_date || '-'} 至 ${props.period.latest_trade_date || '-'}`)
      ])
    }
  }
})

const periodSummary = computed(() => (period: string) =>
  summary.value?.historical_by_period.find(item => item.period === period)
)

const formatNumber = (value?: number) => {
  if (value === undefined || value === null) return '0'
  return value.toLocaleString('zh-CN')
}

const formatDate = (value?: string) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  return date.toLocaleDateString('zh-CN')
}

const fetchCoverage = async () => {
  loading.value = true
  try {
    const response = await getStockSyncCoverage({
      page: page.value,
      page_size: pageSize.value,
      keyword: filters.keyword || undefined,
      source: filters.source || undefined
    })
    if (response.success) {
      items.value = response.data.items
      summary.value = response.data.summary
      total.value = response.data.total
    }
  } finally {
    loading.value = false
  }
}

const search = () => {
  page.value = 1
  fetchCoverage()
}

onMounted(fetchCoverage)
</script>

<style scoped lang="scss">
.stock-sync-coverage {
  .coverage-card {
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

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

  .toolbar {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }

  .summary-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }

  .summary-item {
    padding: 12px;
    border: 1px solid var(--el-border-color-light);
    border-radius: 8px;
    background: var(--el-fill-color-lighter);
  }

  .summary-value {
    font-size: 22px;
    font-weight: 600;
    color: var(--el-text-color-primary);
  }

  .summary-label {
    margin-top: 4px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .cell-stack {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    font-size: 12px;
    color: var(--el-text-color-secondary);
    line-height: 1.3;
  }

  .pagination-row {
    display: flex;
    justify-content: flex-end;
    margin-top: 14px;
  }
}

@media (max-width: 768px) {
  .stock-sync-coverage {
    .summary-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
}
</style>
