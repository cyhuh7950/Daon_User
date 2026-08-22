use daon_user_desktop_lib::workspace_bridge::{
    valid_pdf_upload_for_contract, valid_workspace_id_for_contract,
    valid_workspace_question_input_for_contract, WorkspaceAskQuestionInput,
    WorkspaceOperation, WorkspaceQuestionResult,
};

#[test]
fn workspace_inputs_fail_closed_before_network() {
    assert!(valid_workspace_id_for_contract("workspace-1"));
    assert!(!valid_workspace_id_for_contract("../workspace"));
    assert!(valid_pdf_upload_for_contract(
        "guide.pdf",
        "application/pdf",
        b"%PDF-1.7\nfixture"
    ));
    assert!(!valid_pdf_upload_for_contract(
        "guide.pdf",
        "application/pdf",
        b"not-pdf"
    ));
}

#[test]
fn notebook_scope_is_required_and_rich_citation_shape_is_exact() {
    assert!(serde_json::from_str::<WorkspaceAskQuestionInput>(
        r#"{"workspace_id":"workspace-1","notebook_id":"notebook-1","source_id":"source-1","source_version_id":"version-1","question":"질문"}"#,
    ).is_ok());
    assert!(serde_json::from_str::<WorkspaceAskQuestionInput>(
        r#"{"workspace_id":"workspace-1","source_id":"source-1","source_version_id":"version-1","question":"질문"}"#,
    ).is_err());
    let general: WorkspaceAskQuestionInput = serde_json::from_str(
        r#"{"workspace_id":"workspace-1","notebook_id":"notebook-1","question":"안녕하세요!"}"#,
    ).expect("general conversation shape");
    assert!(valid_workspace_question_input_for_contract(&general));
    for question in ["안녕하세요?", "안녕하세요!"] {
        let input = WorkspaceAskQuestionInput {
            workspace_id: "workspace-1".into(), notebook_id: "notebook-1".into(),
            source_id: None, source_version_id: None, question: question.into(),
        };
        assert!(valid_workspace_question_input_for_contract(&input));
    }
    let factual: WorkspaceAskQuestionInput = serde_json::from_str(
        r#"{"workspace_id":"workspace-1","notebook_id":"notebook-1","question":"2026년 매출은?"}"#,
    ).expect("optional source DTO shape");
    assert!(!valid_workspace_question_input_for_contract(&factual));
    let fullwidth: WorkspaceAskQuestionInput = serde_json::from_str(
        r#"{"workspace_id":"workspace-1","notebook_id":"notebook-1","question":"Ｄａｏｎ 사용법 알려줘"}"#,
    ).expect("fullwidth input shape");
    assert!(!valid_workspace_question_input_for_contract(&fullwidth));
    for question in ["안녕하세요！", "안녕하세요？", "안녕하세요　"] {
        let input = WorkspaceAskQuestionInput {
            workspace_id: "workspace-1".into(), notebook_id: "notebook-1".into(),
            source_id: None, source_version_id: None, question: question.into(),
        };
        assert!(!valid_workspace_question_input_for_contract(&input));
    }
    let rich = r#"{"run_id":"run-1","run_result_id":"result-1","answer":"답변","insufficient":false,"citations":[{"citation_id":"citation-1","source_id":"source-1","source_version_id":"version-1","evidence_span_id":"span-1","page":1,"origin":"raw_source","context_item_id":"source-1","locator":{"kind":"page","value":"1"}}]}"#;
    assert!(serde_json::from_str::<WorkspaceQuestionResult>(rich).is_ok());
    let mut rogue: serde_json::Value = serde_json::from_str(rich).expect("fixture");
    rogue["citations"][0]["internal_url"] = serde_json::json!("https://internal.invalid");
    assert!(serde_json::from_value::<WorkspaceQuestionResult>(rogue).is_err());
}

#[test]
fn operation_surface_is_fixed_and_workspace_bound() {
    let operations = WorkspaceOperation::names_for_contract();
    assert_eq!(
        operations,
        [
            "list_sources",
            "upload_pdf",
            "processing_status",
            "ask_question",
            "citation_content",
            "create_report",
            "list_studio_outputs",
            "get_license",
            "apply_license"
        ]
    );
}
