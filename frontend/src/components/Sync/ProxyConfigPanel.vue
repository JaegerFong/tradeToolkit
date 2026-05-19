<template>
  <el-card class="proxy-config-panel" shadow="never">
    <template #header>
      <div class="panel-header">
        <h3><el-icon><Connection /></el-icon> AKShare 代理配置</h3>
        <el-tag v-if="config.proxy_mode === 'strong'" type="success" size="small">强代理模式</el-tag>
        <el-tag v-else-if="config.proxy_mode === 'basic'" type="warning" size="small">基础代理模式</el-tag>
        <el-tag v-else type="info" size="small">直连模式</el-tag>
      </div>
    </template>

    <el-alert
      type="warning"
      :closable="false"
      show-icon
      class="restart-notice"
      title="配置修改后需重启后端服务生效"
    />

    <el-form :model="config" label-width="140px" class="proxy-form">
      <!-- 代理模式 -->
      <el-form-item label="代理模式">
        <el-radio-group v-model="config.proxy_mode">
          <el-radio-button value="off">直连模式</el-radio-button>
          <el-radio-button value="basic">基础代理</el-radio-button>
          <el-radio-button value="strong">强代理</el-radio-button>
        </el-radio-group>
        <div class="form-tip">
          <span v-if="config.proxy_mode === 'off'">不使用代理，所有请求直连 eastmoney.com</span>
          <span v-else-if="config.proxy_mode === 'basic'">仅使用静态代理列表</span>
          <span v-else>动态代理 API + 静态代理 + 直连兜底（推荐）</span>
        </div>
      </el-form-item>

      <!-- 动态代理 API -->
      <el-form-item v-if="config.proxy_mode === 'strong'" label="代理 API 地址">
        <el-input v-model="config.proxy_api_url" placeholder="https://your-proxy-api.com/get-ips" />
        <div class="form-tip">返回格式：每行一个 ip:port</div>
      </el-form-item>

      <!-- 静态代理 -->
      <el-form-item v-if="config.proxy_mode !== 'off'" label="静态代理列表">
        <el-input
          v-model="config.static_proxies"
          type="textarea"
          :rows="2"
          placeholder="ip:port, ip:port:user:password"
        />
        <div class="form-tip">逗号或换行分隔，支持认证格式 ip:port:user:password</div>
      </el-form-item>

      <!-- 高级选项 -->
      <el-divider content-position="left">高级选项</el-divider>

      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="缓存刷新(秒)">
            <el-input-number v-model="config.proxy_cache_seconds" :min="10" :max="3600" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="轮转轮数">
            <el-input-number v-model="config.proxy_rounds" :min="1" :max="10" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="请求超时(秒)">
            <el-input-number v-model="config.request_timeout" :min="5" :max="120" :step="5" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="请求重试次数">
            <el-input-number v-model="config.request_retries" :min="1" :max="10" />
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="最小请求间隔(秒)">
            <el-input-number v-model="config.min_request_interval" :min="0.1" :max="30" :step="0.1" />
          </el-form-item>
        </el-col>
        <el-col :span="12" />
      </el-row>

      <el-row :gutter="20">
        <el-col :span="12">
          <el-form-item label="TLS 指纹模拟">
            <el-switch v-model="config.use_curl_cffi" active-text="开启" inactive-text="关闭" />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="直连兜底">
            <el-switch v-model="config.include_direct" active-text="开启" inactive-text="关闭" />
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <!-- 操作按钮 -->
    <div class="actions">
      <el-button type="primary" :loading="saving" @click="saveConfig">
        <el-icon><Check /></el-icon>
        保存配置
      </el-button>
      <el-button :loading="testing" @click="testConnection">
        <el-icon><Connection /></el-icon>
        测试连通性
      </el-button>
    </div>

    <!-- 测试结果 -->
    <div v-if="testResults.length > 0" class="test-results">
      <el-divider content-position="left">连通性测试结果</el-divider>
      <div v-for="(r, i) in testResults" :key="i" class="test-item">
        <el-tag :type="r.status === 'success' ? 'success' : r.status === 'error' ? 'danger' : 'warning'" size="small">
          {{ r.type }}
        </el-tag>
        <span class="test-msg">{{ r.message }}</span>
        <span v-if="r.elapsed" class="test-time">{{ r.elapsed }}s</span>
        <div v-if="r.sample" class="test-sample">
          示例: {{ r.sample.join(', ') }}
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { Connection, Check } from '@element-plus/icons-vue'
import { ApiClient } from '@/api/request'

interface ProxyConfig {
  proxy_mode: string
  proxy_api_url: string
  static_proxies: string
  proxy_cache_seconds: number
  proxy_rounds: number
  include_direct: boolean
  request_timeout: number
  request_retries: number
  min_request_interval: number
  use_curl_cffi: boolean
}

const config = reactive<ProxyConfig>({
  proxy_mode: 'strong',
  proxy_api_url: '',
  static_proxies: '',
  proxy_cache_seconds: 60,
  proxy_rounds: 2,
  include_direct: true,
  request_timeout: 20,
  request_retries: 4,
  min_request_interval: 0.8,
  use_curl_cffi: true,
})

const saving = ref(false)
const testing = ref(false)
const testResults = ref<any[]>([])

onMounted(async () => {
  try {
    const res = await ApiClient.get('/api/akshare-init/proxy-config')
    if (res.data?.config) {
      Object.assign(config, res.data.config)
    }
  } catch (e) {
    console.error('获取代理配置失败:', e)
  }
})

async function saveConfig() {
  saving.value = true
  try {
    await ApiClient.post('/api/akshare-init/proxy-config', {
      proxy_mode: config.proxy_mode,
      proxy_api_url: config.proxy_api_url || undefined,
      static_proxies: config.static_proxies || undefined,
      proxy_cache_seconds: config.proxy_cache_seconds,
      proxy_rounds: config.proxy_rounds,
      include_direct: config.include_direct,
      request_timeout: config.request_timeout,
      request_retries: config.request_retries,
      min_request_interval: config.min_request_interval,
      use_curl_cffi: config.use_curl_cffi,
    })
    ElMessage.success('代理配置已保存，重启后端服务后生效')
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    saving.value = false
  }
}

async function testConnection() {
  testing.value = true
  testResults.value = []
  try {
    const res = await ApiClient.post('/api/akshare-init/proxy-config/test', {}, { timeout: 30000 })
    if (res.data?.tests) {
      testResults.value = res.data.tests
    }
  } catch (e: any) {
    ElMessage.error(`测试失败: ${e.message}`)
  } finally {
    testing.value = false
  }
}
</script>

<style scoped lang="scss">
.proxy-config-panel {
  .panel-header {
    display: flex; align-items: center; gap: 12px;
    h3 { margin: 0; display: flex; align-items: center; gap: 8px; }
  }
  .restart-notice { margin-bottom: 16px; }
  .proxy-form { margin-top: 16px; }
  .form-tip { font-size: 12px; color: var(--el-text-color-secondary); margin-top: 4px; }
  .actions { display: flex; gap: 12px; margin-top: 16px; }
  .test-results { margin-top: 16px; }
  .test-item {
    display: flex; align-items: center; gap: 8px; padding: 6px 0; flex-wrap: wrap;
    .test-msg { flex: 1; font-size: 13px; }
    .test-time { font-size: 12px; color: var(--el-text-color-secondary); }
    .test-sample { width: 100%; font-size: 12px; color: var(--el-text-color-secondary); margin-top: 2px; }
  }
}
</style>
