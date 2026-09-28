view: customers {
  sql_table_name: raw.customers ;;

  dimension: customer_id {
    primary_key: yes
    hidden: yes
    sql: ${TABLE}.customer_id ;;
  }

  dimension: contact_email {
    description: "Contact email."
    sql: ${TABLE}.email ;;
  }

  dimension: phone {
    description: "Contact phone."
    sql: ${TABLE}.phone ;;
    tags: ["pii", "pii:phone"]
  }

  dimension: ssn {
    description: "Synthetic tax id."
    sql: ${TABLE}.ssn ;;
    required_access_grants: [can_view_pii]
    tags: ["pii", "pii:ssn"]
  }

  dimension: is_test_account {
    hidden: yes
    type: yesno
    sql: ${TABLE}.is_test_account ;;
  }
}
