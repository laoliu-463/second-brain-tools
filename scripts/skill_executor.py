"""
Skill执行引擎
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import json
from datetime import datetime

from .skill_router import SkillRouter, SkillPlan, SkillCapability


@dataclass
class SkillExecutionResult:
    """Skill执行结果"""
    success: bool
    skill_name: str
    outputs: Dict[str, Any]
    errors: List[str]
    execution_time: float
    timestamp: datetime
    metadata: Dict[str, Any]


@dataclass
class SkillExecutionContext:
    """Skill执行上下文"""
    knowledge_root: Path
    question: str
    parameters: Dict[str, Any]
    intermediate_results: Dict[str, Any] = field(default_factory=dict)
    logs: List[str] = field(default_factory=list)


class SkillExecutor:
    """Skill执行引擎"""
    
    def __init__(self, router: SkillRouter):
        self.router = router
        self._execution_history: List[SkillExecutionResult] = []
    
    def execute_plan(self, plan: SkillPlan, context: SkillExecutionContext) -> SkillExecutionResult:
        """执行Skill计划"""
        start_time = datetime.now()
        skill_chain = [plan.primary_skill] + plan.secondary_skills
        
        # 执行主Skill
        result = self._execute_single_skill(
            plan.primary_skill,
            context.parameters,
            context
        )
        
        # 执行辅助Skill
        for skill_name in plan.secondary_skills:
            secondary_result = self._execute_single_skill(
                skill_name,
                context.parameters,
                context
            )
            # 将主Skill结果传递给辅助Skill
            context.intermediate_results[plan.primary_skill] = result.outputs
        
        execution_time = (datetime.now() - start_time).total_seconds()
        
        # 验证成功标准
        validation_passed = self._validate_success_criteria(
            result.outputs,
            plan.success_criteria
        )
        
        final_result = SkillExecutionResult(
            success=validation_passed,
            skill_name=plan.primary_skill,
            outputs=result.outputs,
            errors=result.errors,
            execution_time=execution_time,
            timestamp=datetime.now(),
            metadata={
                "question": context.question,
                "plan_type": plan.question_type.value,
                "skills_executed": skill_chain
            }
        )
        
        self._execution_history.append(final_result)
        return final_result
    
    def _execute_single_skill(
        self,
        skill_name: str,
        parameters: Dict[str, Any],
        context: SkillExecutionContext
    ) -> SkillExecutionResult:
        """执行单个Skill"""
        start_time = datetime.now()
        capability = self.router.get_skill_capability(skill_name)
        
        if capability is None:
            return SkillExecutionResult(
                success=False,
                skill_name=skill_name,
                outputs={},
                errors=[f"Skill {skill_name} not found in registry"],
                execution_time=0,
                timestamp=datetime.now(),
                metadata={}
            )
        
        context.logs.append(f"开始执行Skill: {skill_name}")
        
        try:
            # 根据Skill类型执行不同操作
            if skill_name == "kb-retriever":
                outputs = self._execute_kb_retriever(parameters, context)
            elif skill_name == "second-brain-maintainer":
                outputs = self._execute_second_brain_maintainer(parameters, context)
            elif skill_name == "obsidian-bases":
                outputs = self._execute_obsidian_bases(parameters, context)
            elif skill_name == "obsidian-cli":
                outputs = self._execute_obsidian_cli(parameters, context)
            elif skill_name == "obsidian-markdown":
                outputs = self._execute_obsidian_markdown(parameters, context)
            else:
                outputs = {}
                context.logs.append(f"Skill {skill_name} 暂无具体实现")
            
            execution_time = (datetime.now() - success_time).total_seconds()
            
            return SkillExecutionResult(
                success=True,
                skill_name=skill_name,
                outputs=outputs,
                errors=[],
                execution_time=execution_time,
                timestamp=datetime.now(),
                metadata={}
            )
            
        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            return SkillExecutionResult(
                success=False,
                skill_name=skill_name,
                outputs={},
                errors=[str(e)],
                execution_time=execution_time,
                timestamp=datetime.now(),
                metadata={}
            )
    
    def _execute_kb_retriever(self, parameters: Dict[str, Any], context: SkillExecutionContext) -> Dict[str, Any]:
        """执行知识库检索"""
        query = parameters.get("query", "")
        limit = parameters.get("limit", 20)
        collection = parameters.get("collection")
        
        # 使用Grep搜索知识库
        from pathlib import Path
        knowledge_root = context.knowledge_root
        
        # 搜索Markdown文件
        pattern = query
        if collection:
            # 如果指定collection，缩小搜索范围
            if collection == "renzhi55":
                search_path = knowledge_root / "人智55篇" / "正文"
            elif collection == "courses":
                search_path = knowledge_root / "大小课" / "课程正文"
            else:
                search_path = knowledge_root
        else:
            search_path = knowledge_root
        
        # 使用Grep搜索（实际需要调用Grep工具）
        # 这里返回模拟结果
        results = [
            {"logical_uri": f"kb://renzhi55/test", "title": f"包含'{query}'的文档"}
        ]
        
        context.logs.append(f"在 {search_path} 中搜索: {query}")
        
        return {
            "query": query,
            "count": len(results),
            "results": results
        }
    
    def _execute_second_brain_maintainer(self, parameters: Dict[str, Any], context: SkillExecutionContext) -> Dict[str, Any]:
        """执行第二大脑维护"""
        mode = parameters.get("mode", "query")
        
        if mode == "query":
            # 查询状态
            return {
                "mode": "query",
                "status": "healthy",
                "documents_count": 95
            }
        elif mode == "ingest":
            # 摄入新资料
            return {
                "mode": "ingest",
                "ingested_count": 0
            }
        else:
            return {
                "mode": mode,
                "status": "unknown"
            }
    
    def _execute_obsidian_bases(self, parameters: Dict[str, Any], context: SkillExecutionContext) -> Dict[str, Any]:
        """执行Obsidian Bases操作"""
        base_name = parameters.get("base_name", "test")
        
        # 创建Base文件（模拟）
        base_path = context.knowledge_root / f"{base_name}.base"
        
        context.logs.append(f"创建Base文件: {base_path}")
        
        return {
            "base_name": base_name,
            "base_path": str(base_path),
            "status": "created"
        }
    
    def _execute_obsidian_cli(self, parameters: Dict[str, Any], context: SkillExecutionContext) -> Dict[str, Any]:
        """执行Obsidian CLI操作"""
        # Obsidian需要运行中，这里返回状态
        return {
            "status": "obsidian_not_running",
            "message": "需要Obsidian运行中才能执行CLI命令"
        }
    
    def _execute_obsidian_markdown(self, parameters: Dict[str, Any], context: SkillExecutionContext) -> Dict[str, Any]:
        """执行Obsidian Markdown操作"""
        content = parameters.get("content", "")
        file_path = parameters.get("file_path")
        
        if file_path:
            context.logs.append(f"创建Markdown文件: {file_path}")
            return {
                "action": "create",
                "file_path": file_path,
                "status": "created"
            }
        else:
            return {
                "action": "no_file_path",
                "status": "pending"
            }
    
    def _validate_success_criteria(self, outputs: Dict[str, Any], criteria: List) -> bool:
        """验证成功标准"""
        if not criteria:
            return True
        
        for criterion in criteria:
            if not criterion(outputs):
                return False
        
        return True
    
    def get_execution_history(self) -> List[SkillExecutionResult]:
        """获取执行历史"""
        return self._execution_history
    
    def get_execution_stats(self) -> Dict[str, Any]:
        """获取执行统计"""
        if not self._execution_history:
            return {"total_executions": 0}
        
        successful = sum(1 for r in self._execution_history if r.success)
        failed = len(self.execution_history) - successful
        
        return {
            "total_executions": len(self._execution_history),
            "successful": successful,
            "failed": failed,
            "success_rate": successful / len(self.execution_history) if self._execution_history else 0
        }


def create_executor(router: SkillRouter) -> SkillExecutor:
    """创建Skill执行引擎实例"""
    return SkillExecutor(router)