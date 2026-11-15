<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { useApiContext } from '../api/context'
import type { RegisteredProject, RunnerConfig } from '../api/contracts'
defineProps<{ title?: string }>()
const emit = defineEmits<{ ready: [project: RegisteredProject | null] }>()
const { api, mock } = useApiContext()
const uid = useId(), label = ref(''), root = ref(''), file = ref<File>(), error = ref(''), busy = ref(false)
const project = ref<RegisteredProject>(), runtimes = ref<string[]>([]), python = ref('')
const targets = ref(''), paths = ref('.\nsrc'), plugins = ref(''), deselect = ref(''), seconds = ref(300), dirty = ref(false)
const controller = new AbortController()
onMounted(async () => { try { runtimes.value = (await api.runtimeOptions?.(controller.signal))?.python ?? [] } catch { /* Upload remains available. */ } })
onBeforeUnmount(() => controller.abort())
function changed() { dirty.value = true; emit('ready', null) }
function selectFile(event: Event) { file.value = (event.target as HTMLInputElement).files?.[0]; project.value = undefined; emit('ready', null) }
const lines = (text: string) => text.split('\n').map(s => s.trim()).filter(Boolean)
async function upload() {
  if (!file.value || busy.value || !api.uploadProject) return
  if (file.value.size > 64 * 1024 * 1024) { error.value = 'ZIP 文件最多 64 MiB。'; return }
  busy.value = true; error.value = ''; emit('ready', null)
  try {
    project.value = await api.uploadProject(file.value, label.value || file.value.name.replace(/\.zip$/i, ''), root.value, controller.signal)
    const config = project.value.runner_config
    if (config) { python.value = config.python_executable || ''; paths.value = config.python_paths.join('\n'); targets.value = config.test_targets.join('\n'); deselect.value = config.deselect.join('\n'); plugins.value = config.pytest_plugins.join('\n'); seconds.value = config.timeout_seconds }
    dirty.value = !!config?.python_version && !config.python_executable
    emit('ready', dirty.value ? null : project.value)
  } catch (cause) { if (!controller.signal.aborted) error.value = cause instanceof Error ? cause.message : '上传失败' }
  finally { busy.value = false }
}
async function configure() {
  if (!project.value || !api.configureProject || busy.value) return
  busy.value = true; error.value = ''
  const config: RunnerConfig = { ...project.value.runner_config, python_executable: python.value || null, python_paths: lines(paths.value), test_targets: lines(targets.value),
    deselect: lines(deselect.value), pytest_plugins: lines(plugins.value), timeout_seconds: Number(seconds.value) }
  try {
    project.value = await api.configureProject(project.value.id, config, controller.signal)
    dirty.value = false; emit('ready', project.value)
  } catch (cause) { if (!controller.signal.aborted) error.value = cause instanceof Error ? cause.message : '保存配置失败' }
  finally { busy.value = false }
}
</script>
<template>
  <section class="upload-box" :aria-label="title || '上传项目'">
    <h3>{{ title || '上传自己的项目' }}</h3>
    <p class="field-hint">ZIP · 最大 64 MiB · 自动识别 Python、Java、Go、C++。修复在独立副本中进行。</p>
    <label :for="uid + '-file'">项目压缩包</label>
    <input :id="uid + '-file'" type="file" accept=".zip,application/zip" :disabled="busy || mock" @change="selectFile" />
    <div class="form-row">
      <div><label :for="uid + '-name'">项目名称</label><input :id="uid + '-name'" v-model="label" placeholder="使用文件名" :disabled="busy" /></div>
      <div><label :for="uid + '-root'">项目子目录（可选）</label><input :id="uid + '-root'" v-model="root" placeholder="自动识别 ZIP 根目录" :disabled="busy" /></div>
    </div>
    <button type="button" class="button secondary" :disabled="!file || busy || mock" @click="upload">{{ busy ? '正在处理…' : '导入项目' }}</button>
    <p v-if="mock" class="warning-notice">上传和真实实验需要后端连接模式。</p>
    <div v-if="project" class="upload-receipt" role="status">
      <strong>{{ project.label }} · {{ ({java:'Java', python:'Python', go:'Go', cpp:'C++'})[project.language] }}</strong>
      <span>{{ project.file_count ? `${project.file_count} 个文件 · ` : '' }}{{ dirty ? '测试配置有改动，请保存' : '已导入，可提交任务' }}</span>
      <small>源码指纹 {{ project.source_digest?.slice(0, 16) }}</small>
      <small v-if="project.runner_config?.python_version">要求 Python {{ project.runner_config.python_version }} · {{ project.runner_config.python_executable ? '已选择运行环境，可展开查看解释器与测试范围' : '未找到完全匹配的环境，请在下方选择已安装所需依赖的解释器' }}</small>
    </div>
    <details v-if="project" class="runner-options" :open="dirty">
      <summary>测试与运行环境设置</summary>
      <p class="field-hint">Python 使用已安装依赖的解释器；Go 使用 go test ./...；C++ 使用 CMake + CTest；Java 使用 Maven。依赖或编译器缺失会显示真实错误。</p>
      <template v-if="project.language === 'python'">
        <label :for="uid + '-python'">Python 解释器</label><select :id="uid + '-python'" v-model="python" @change="changed"><option value="">后端默认环境</option><option v-for="path in runtimes" :key="path" :value="path">{{ path }}</option></select>
        <label :for="uid + '-custom-python'">或指定解释器完整路径</label><input :id="uid + '-custom-python'" v-model="python" placeholder="D:/my-project/.venv/Scripts/python.exe" @input="changed" />
        <label :for="uid + '-targets'">验收测试路径（每行一项，留空执行全部测试）</label><textarea :id="uid + '-targets'" v-model="targets" rows="3" placeholder="tests/test_feature.py" @input="changed" />
        <label :for="uid + '-paths'">源码导入目录（每行一项）</label><textarea :id="uid + '-paths'" v-model="paths" rows="2" @input="changed" />
        <label :for="uid + '-plugins'">额外 pytest 插件（每行一项）</label><textarea :id="uid + '-plugins'" v-model="plugins" rows="2" placeholder="pytester" @input="changed" />
        <label :for="uid + '-deselect'">明确排除的测试节点（计入实验配置，不算通过）</label><textarea :id="uid + '-deselect'" v-model="deselect" rows="2" placeholder="tests/test_optional.py::test_external_dependency" @input="changed" />
      </template>
      <label :for="uid + '-seconds'">单轮测试最长秒数</label><input :id="uid + '-seconds'" v-model="seconds" type="number" min="10" max="900" @input="changed" />
      <button class="button secondary" type="button" :disabled="busy || !dirty" @click="configure">保存测试配置</button>
    </details>
    <p v-if="error" role="alert" class="error-notice">{{ error }}</p>
  </section>
</template>
