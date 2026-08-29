"""
问题分类器和Skill注册表
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Callable, Any
from enum import Enum


class QuestionType(Enum):
    """问题类型枚举"""
    KNOWLEDGE_SEARCH = "knowledge_search"  # 知识检索
    KNOWLEDGE_ORGANIZE = "knowledge_organize"  # 知识整理
    DATABASE_CREATE = "database_create"  # 数据库创建
    DOCUMENT_EDIT = "document_edit"  # 文档编辑
    SYSTEM_MAINTENANCE = "system_maintenance"  # 系统维护


@dataclass
class SkillCapability:
    """Skill能力描述"""
    skill_name: str
    skill_path: str
    capabilities: List[str]  # 能做的事
    required_params: Dict[str, type]  # 必需参数
    optional_params: Dict[str, type]  # 可选参数
    dependencies: List[str]  # 依赖的其他Skill


@dataclass
class SkillPlan:
    """Skill执行计划"""
    question_type: QuestionType
    primary_skill: str
    secondary_skills: List[str]
    parameters: Dict[str, Any]
    success_criteria: List[Callable[[Any], bool]]


class SkillRouter:
    """Skill路由器"""
    
    def __init__(self):
        self._skill_registry: Dict[str, SkillCapability] = {}
        self._question_rules: Dict[QuestionType, str] = {}
        self._orchestration_templates: Dict[str, List[str]] = {}
        self._load_registry()
        self._load_rules()
        self._load_templates()
    
    def _load_registry(self):
        """加载Skill注册表"""
        # kb-retriever
        self._skill_registry["kb-retriever"] = SkillCapability(
            skill_name="kb-retriever",
            skill_path="C:\\Users\\caojianing\\.claude\\skills\\kb-retriever\\SKILL.md",
            capabilities=[
                "检索知识库",
                "查询文档",
                "获取目录结构",
                "渐进式检索"
            ],
            required_params={"query": str},
            optional_params={"limit": int, "collection": str},
            dependencies=[]
        )
        
        # second-brain-maintainer
        self._skill_registry["second-brain-maintainer"] = SkillCapability(
            skill_name="second-brain-maintainer",
            skill_path="C:\\Users\\caojianing\\.cursor\\skills-cursor\\second-brain-maintainer\\SKILL.md",
            capabilities=[
                "初始化知识库",
                "摄入新资料",
                "查询知识",
                "健康检查",
                "修复断链"
            ],
            required_params={},
            optional_params={"mode": str},
            dependencies=[]
        )
        
        # obsidian-bases
        self._skill_registry["obsidian-bases"] = SkillCapability(
            skill_name="obsidian-bases",
            skill_path="C:\\Users\\caojianing\\.claude\\skills\\obsidian-bases\\SKILL.md",
            capabilities=[
                "创建Base文件",
                "配置过滤器",
                "添加公式",
                "配置视图"
            ],
            required_params={"base_name": str},
            optional_params={"filters": str, "formulas": str, "views": str},
            dependencies=[]
        )
        
        # obsidian-cli
        self._skill_registry["obsidian-cli"] = SkillCapability(
            skill_name="obsidian-cli",
            skill_path="C:\\Users\\caojianing\\.claude\\skills\\obsidian-cli\\SKILL.md",
            capabilities=[
                "读取笔记",
                "创建笔记",
                "搜索笔记",
                "设置属性",
                "读取属性"
            ],
            required_params={},
            optional_params={"file": str, "path": str, "content": str},
            dependencies=[]
        )
        
        # obsidian-markdown
        self._skill_registry["obsidian-markdown"] = SkillCapability(
            skill_name="obsidian-markdown",
            skill_path="C:\\Users\\caojianing\\.claude\\skills\\obsidian-markdown\\SKILL.md",
            capabilities=[
                "创建Markdown笔记",
                "添加wikilinks",
                "嵌入内容",
                "添加callouts",
                "配置frontmatter"
            ],
            required_params={},
            optional_params={"content": str, "file_path": str},
            dependencies=[]
        )
    
    def _load_rules(self):
        """加载问题分类规则"""
        self._question_rules = {
            QuestionType.KNOWLEDGE_SEARCH: "kb-retriever",
            QuestionType.KNOWLEDGE_ORGANIZE: "second-brain-maintainer",
            QuestionType.DATABASE_CREATE: "obsidian-bases",
            QuestionType.DOCUMENT_EDIT: "obsidian-markdown",
            QuestionType.SYSTEM_MAINTENANCE: "second-brain-maintainer"
        }
    
    def _load_templates(self):
        """加载Skill编排模板"""
        # 检索-整理模式
        self._orchestration_templates["search-organize"] = [
            "kb-retriever",
            "second-brain-maintainer"
        ]
        
        # 检索-编辑模式
        self._orchestration_templates["search-edit"] = [
            "kb-reiterver",
            "obsidian-markdown",
            "obsidian-cli"
        ]
        
        # 创建-关联模式
        self._orchestration_templates["create-associate"] = [
            "obsidian-markdown",
            "obsidian-bases",
            "obsidian-cli"
        ]
    
    def classify_question(self, question: str, context: Optional[Dict[str, Any]] = None) -> QuestionType:
        """分类问题类型"""
        question_lower = question.lower()
        
        # 关键词匹配
        if any(keyword in question_lower for keyword in ["查找", "搜索", "检索", "查询"]):
            return QuestionType.KNOWLEDGE_SEARCH
        elif any(keyword in question_lower for keyword in ["整理", "组织", "分类", "归纳"]):
            return QuestionType.KNOWLEDGE_ORGANIZE
        elif any(keyword in question_lower for keyword in ["创建base", "数据库", "表", "视图"]):
            return QuestionType.DATABASE_CREATE
        elif any(keyword in question_lower for keyword in ["编辑", "修改", "添加", "删除"]):
            return QuestionType.DOCUMENT_EDIT
        elif any(keyword in question_lower for keyword in ["检查", "维护", "验证", "清理"]):
            return QuestionType.SYSTEM_MAINTENANCE
        
        # 默认返回知识检索
        return QuestionType.KNOWLEDGE_SEARCH
    
    def route(self, question: str, context: Optional[Dict[str, Any]] = None) -> SkillPlan:
        """路由到Skill计划"""
        question_type = self.classify_question(question, context)
        primary_skill = self._question_rules[question_type]
        
        # 根据具体场景选择编排模板
        if question_type == QuestionType.KNOWLEDGE_SEARCH and context and "整理" in question:
            template = self._orchestration_templates["search-organize"]
        elif question_type == QuestionType.DOCUMENT_EDIT and context and "关联" in question:
            template = self._orchestration_templates["create-associate"]
        else:
            template = [primary_skill]
        
        return SkillPlan(
            question_type=question_type,
            primary_skill=primary_skill,
            secondary_skills=template[1:] if len(template) > 1 else [],
            parameters={"question": question},
            success_criteria=[]
        )
    
    def get_skill_capability(self, skill_name: str) -> Optional[SkillCapability]:
        """获取Skill能力描述"""
        return self._skill_registry.get(skill_name)
    
    def list_all_skills(self) -> List[SkillCapability]:
        """列出所有注册的Skill"""
        return list(self._skill_registry.values())


def create_router() -> SkillRouter:
    """创建Skill路由器实例"""
    return SkillRouter()