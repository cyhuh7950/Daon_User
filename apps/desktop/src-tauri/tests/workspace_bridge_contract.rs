use daon_user_desktop_lib::workspace_bridge::{
    valid_pdf_upload_for_contract, valid_workspace_id_for_contract, WorkspaceOperation,
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
            "list_studio_outputs"
        ]
    );
}
