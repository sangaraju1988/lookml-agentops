view: customers {
  sql_table_name: raw.customers ;;
  label: "Customers"
  description: "Business customers of Harborline Supply Co. Contains synthetic PII."

  dimension: customer_id {
    primary_key: yes
    hidden: yes
    type: number
    sql: ${TABLE}.customer_id ;;
  }

  dimension: company_name {
    type: string
    label: "Customer Name"
    description: "Legal name of the customer company."
    sql: ${TABLE}.company_name ;;
    tags: ["ai_exposed", "glossary:customer"]
  }

  dimension: contact_name {
    hidden: yes
    type: string
    label: "Contact Name"
    description: "Primary contact person at the customer."
    sql: ${TABLE}.contact_name ;;
    tags: ["pii", "pii:name"]
  }

  dimension: email {
    type: string
    label: "Contact Email"
    description: "Email address of the primary contact."
    sql: ${TABLE}.email ;;
    required_access_grants: [can_view_pii]
    tags: ["pii", "pii:email", "ai_hidden"]
  }

  dimension: phone {
    hidden: yes
    type: string
    label: "Contact Phone"
    description: "Phone number of the primary contact."
    sql: ${TABLE}.phone ;;
    tags: ["pii", "pii:phone"]
  }

  dimension: date_of_birth {
    hidden: yes
    type: date
    label: "Contact Date of Birth"
    description: "Date of birth of the primary contact."
    sql: ${TABLE}.date_of_birth ;;
    tags: ["pii", "pii:dob"]
  }

  dimension: billing_address {
    hidden: yes
    type: string
    label: "Billing Address"
    description: "Billing street address."
    sql: ${TABLE}.billing_address ;;
    tags: ["pii", "pii:address"]
  }

  dimension: segment {
    type: string
    label: "Customer Segment"
    description: "Enterprise, Mid-Market or SMB."
    sql: ${TABLE}.segment ;;
    tags: ["ai_exposed", "glossary:customer_segment"]
  }

  dimension: region_id {
    hidden: yes
    type: number
    sql: ${TABLE}.region_id ;;
  }

  dimension: is_test_account {
    hidden: yes
    type: yesno
    description: "Internal QA account. Always excluded from reporting."
    sql: ${TABLE}.is_test_account ;;
  }

  measure: customer_count {
    type: count
    label: "Customer Count"
    description: "Number of customers."
    value_format_name: decimal_0
  }
}
