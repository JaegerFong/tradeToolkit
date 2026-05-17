<template>
  <div class="pattern-screening-page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">技术形态选股</h1>
        <p class="page-description">
          基于本地A股日线数据扫描技术形态，首期支持老鸭头与N字形态。结果仅供研究与教育用途，不构成投资建议。
        </p>
      </div>
    </div>

    <el-card shadow="never" class="filter-card">
      <template #header>
        <div class="card-header">
          <span>筛选参数</span>
          <div class="header-actions">
            <el-switch v-model="form.llm.enabled" active-text="启用LLM复核" />
            <el-button type="primary" :loading="submitting" @click="submitTask">开始选股</el-button>
          </div>
        </div>
      </template>

      <el-form :model="form" label-width="120px" class="filter-form">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="形态选择">
              <el-checkbox-group v-model="form.pattern_types">
                <el-checkbox label="laoyatou">老鸭头</el-checkbox>
                <el-checkbox label="n_shape">N字形态</el-checkbox>
              </el-checkbox-group>
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="时间窗口">
              <el-input-number v-model="form.window.lookback_days" :min="30" :max="365" />
              <span class="inline-hint">天（日线）</span>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="最小市值">
              <el-input-number v-model="form.universe.min_market_cap" :min="0" :step="1000000000" />
              <span class="inline-hint">元（0 表示不限制）</span>
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="LLM复核数量">
              <el-input-number v-model="form.llm.max_reviews" :min="0" :max="500" />
              <span class="inline-hint">只（0 表示不复核）</span>
            </el-form-item>
          </el-col>
        </el-row>

        <el-divider>规则参数（首期通用）</el-divider>

        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="最小涨幅">
              <el-input-number v-model="form.rules.min_up_pct" :min="0" :max="5" :step="0.01" />
              <span class="inline-hint">（例如 0.15 表示 15%）</span>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="最大回撤">
              <el-input-number v-model="form.rules.max_drawdown" :min="0" :max="1" :step="0.01" />
              <span class="inline-hint">（例如 0.5 表示 50%）</span>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="整理缩量比">
              <el-input-number v-model="form.rules.consolidation_volume_ratio" :min="0" :max="5" :step="0.05" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="突破放量比">
              <el-input-number v-model="form.rules.breakout_volume_ratio" :min="0" :max="20" :step="0.05" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </el-card>

    <el-card shadow="never" class="next-card">
      <template #header>
        <div class="card-header">
          <span>下一步</span>
        </div>
      </template>
      <div class="next-content">
        <p>提交任务后，将跳转到任务详情页，你可以查看选股过程事件、结果列表，并点击股票查看入选分析。</p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { patternScreeningApi, type PatternScreeningCreateReq } from '@/api/patternScreening'

const router = useRouter()
const submitting = ref(false)

type PatternScreeningForm = {
  pattern_types: PatternScreeningCreateReq['pattern_types']
  market: 'CN'
  universe: NonNullable<PatternScreeningCreateReq['universe']>
  window: NonNullable<PatternScreeningCreateReq['window']>
  rules: NonNullable<PatternScreeningCreateReq['rules']>
  llm: NonNullable<PatternScreeningCreateReq['llm']>
}

const form = reactive<PatternScreeningForm>({
  pattern_types: ['laoyatou'],
  market: 'CN',
  universe: { board: ['MAIN', 'STAR', 'CHINEXT'], min_market_cap: 0, industries: [] },
  window: { end_date: 'auto', lookback_days: 90 },
  rules: {
    min_up_pct: 0.15,
    max_drawdown: 0.5,
    consolidation_volume_ratio: 0.75,
    breakout_volume_ratio: 1.3
  },
  llm: { enabled: true, max_reviews: 50 }
})

async function submitTask() {
  if (!form.pattern_types.length) {
    ElMessage.warning('请选择至少一种形态')
    return
  }

  submitting.value = true
  try {
    // 兼容：0 表示不限制
    const payload: PatternScreeningCreateReq = JSON.parse(JSON.stringify(form))
    if (payload.universe?.min_market_cap === 0) payload.universe.min_market_cap = null
    if (payload.llm?.max_reviews === 0) payload.llm.enabled = false

    const res = await patternScreeningApi.createTask(payload)
    ElMessage.success('任务已创建，正在跳转…')
    await router.push({ name: 'PatternScreeningTaskDetail', params: { taskId: res.task_id } })
  } catch (e: any) {
    ElMessage.error(e?.message ?? '创建任务失败')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped lang="scss">
.pattern-screening-page {
  .filter-card,
  .next-card {
    margin-bottom: 16px;
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .inline-hint {
    margin-left: 8px;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
}
</style>
