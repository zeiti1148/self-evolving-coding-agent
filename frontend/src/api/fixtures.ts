import type { Experience, MockScenario, TaskStep } from './contracts'

export const javaFile = 'src/main/java/dev/selfevolving/benchmark/GreetingService.java'
const originalJava = `package dev.selfevolving.benchmark;

public class GreetingService {
    public String greet(String name) {
        return "Hello, " + name.trim() + "!";
    }
}
`
const repairedJava = `package dev.selfevolving.benchmark;

public class GreetingService {
    public String greet(String name) {
        if (name == null) {
            return "Hello, guest!";
        }
        return "Hello, " + name.trim() + "!";
    }
}
`

export function seedExperiences(now: number): Experience[] {
  const samples = [
    ['为问候方法补充空值分支', 'NullPointerException', 'name 为 null 时直接调用 trim()。', '在调用 trim() 之前处理 null，返回默认访客问候。', 'java', '空值检查'],
    ['修复集合遍历时的并发修改', 'ConcurrentModificationException', '增强 for 循环中直接删除元素。', '通过迭代器删除匹配元素，避免修改遍历中的集合结构。', 'java', '集合'],
    ['统一金额计算的舍入规则', 'AssertionError', '金额在不同计算步骤使用了不同精度。', '统一使用 BigDecimal，并在业务边界执行约定的舍入。', 'java', '精度'],
    ['处理空列表的首元素访问', 'IndexOutOfBoundsException', '未检查列表长度便读取首元素。', '为无结果情况添加明确分支，保留非空列表原有行为。', 'java', '边界条件'],
    ['关闭文件读取后的资源', 'IOException', '异常分支没有释放输入流。', '使用 try-with-resources 管理输入流生命周期。', 'java', '资源管理'],
    ['修复日期边界的比较', 'AssertionError', '结束日期被错误地排除在有效区间之外。', '使用包含边界的比较逻辑，覆盖同日与跨月输入。', 'java', '日期'],
    ['过滤空白的用户名称', 'IllegalArgumentException', '输入包含空白，但只校验了字符串长度。', '在校验之前规范化输入，并区分空值与空白字符串。', 'java', '输入校验'],
  ]
  return samples.map((sample, index) => ({
    id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
    task_summary: sample[0]!, error_type: sample[1]!, root_cause: sample[2]!, solution: sample[3]!,
    verification: '演示验证记录：相关边界用例与回归用例均通过。', tags: sample.slice(4),
    source_task_id: null, created_at: new Date(now - (index + 1) * 86400000).toISOString(),
  }))
}

export function makeSteps(createdAt: number, stepMs: number, scenario: MockScenario): TaskStep[] {
  if (scenario === 'schedule-error') return []
  const specifications: Array<[string, Record<string, unknown>, string, string | null]> = [
    ['list_files', { path: '.', recursive: true }, `${javaFile}\nsrc/test/java/dev/selfevolving/benchmark/GreetingServiceTest.java`, null],
    ['read_file', { path: javaFile }, originalJava, null],
    ['run_tests', { project_path: '.' }, '[ERROR] GreetingServiceTest.greetsNullName\njava.lang.NullPointerException: name is null\nTests run: 2, Failures: 0, Errors: 1\n', 'TEST_FAILED'],
  ]
  if (scenario !== 'stopped') specifications.push(
    ['write_file', { path: javaFile, content: repairedJava }, '文件修改已写入任务工作副本。', null],
    ['run_tests', { project_path: '.' }, scenario === 'tests-failed'
      ? '[ERROR] GreetingServiceTest.greetsNullName\nTests run: 2, Failures: 1, Errors: 0\nBUILD FAILURE\n'
      : '[INFO] Running GreetingServiceTest\nTests run: 2, Failures: 0, Errors: 0, Skipped: 0\nBUILD SUCCESS\n', scenario === 'tests-failed' ? 'TEST_FAILED' : null],
  )
  else specifications.push(['run_tests', { project_path: '.' }, '最终验证超时，未得到完整测试结果。', 'COMMAND_TIMEOUT'])
  return specifications.map(([name, args, output, error], index) => {
    const id = crypto.randomUUID()
    return { sequence: index + 1, call: { id, name, arguments: args },
      result: { call_id: id, tool_name: name, success: !error, output, error_code: error,
        error_message: error ? (error === 'TEST_FAILED' ? '测试用例未通过。' : '测试执行超时。') : null,
        metadata: { ...(name === 'run_tests' ? { exit_code: error ? 1 : 0 } : {}), truncated: false } },
      completed_at: new Date(createdAt + (index + 2) * stepMs).toISOString(),
    }
  })
}
